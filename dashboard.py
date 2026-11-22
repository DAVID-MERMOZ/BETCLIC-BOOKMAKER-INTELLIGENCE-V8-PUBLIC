"""Tableau de bord local Betclic V8.1 — lecture seule sur la base privée."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

DB_PATH = Path("data/betclic_history.db")
PREDICTIONS_PATH = Path("reports/team_form_predictions.csv")


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


@st.cache_data(ttl=60)
def load_dashboard_data(db_path, refresh_key):
    path = Path(db_path)
    if not path.is_file():
        return {"error": f"Base SQLite introuvable : {path}"}
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        odds = conn.execute("SELECT COUNT(*) FROM odds_history").fetchone()[0]
        upcoming = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE COALESCE(is_live,0)=0 AND match_date>?",
            (now,),
        ).fetchone()[0]
        alerts = 0
        alert_rows = []
        if table_exists(conn, "odds_alerts"):
            alerts = conn.execute("SELECT COUNT(*) FROM odds_alerts").fetchone()[0]
            alert_rows = conn.execute("""
                SELECT match_name, market_name, selection_name, alert_type,
                       direction, variation, observed_at
                FROM odds_alerts ORDER BY observed_at DESC LIMIT 100
            """).fetchall()
        confirmed = 0
        if table_exists(conn, "validated_match_links"):
            confirmed = conn.execute(
                "SELECT COUNT(DISTINCT betclic_match_id) FROM validated_match_links "
                "WHERE validation_status='CONFIRMED'"
            ).fetchone()[0]
        fixtures = conn.execute("""
            SELECT m.match_id, m.match_name, m.match_date,
                   COUNT(o.id) AS snapshots
            FROM matches m LEFT JOIN odds_history o ON o.match_id=m.match_id
            WHERE COALESCE(m.is_live,0)=0 AND m.match_date>?
            GROUP BY m.match_id, m.match_name, m.match_date
            ORDER BY m.match_date LIMIT 100
        """, (now,)).fetchall()
        market_names = [r[0] for r in conn.execute(
            "SELECT DISTINCT market_name FROM odds_history ORDER BY market_name"
        ).fetchall() if r[0]]
        match_options = conn.execute("""
            SELECT m.match_id, m.match_name, m.match_date
            FROM matches m WHERE EXISTS (
                SELECT 1 FROM odds_history o WHERE o.match_id=m.match_id
            ) ORDER BY m.match_date DESC LIMIT 250
        """).fetchall()
    return {
        "matches": matches, "odds": odds, "upcoming": upcoming,
        "alerts": alerts, "confirmed": confirmed,
        "alert_rows": alert_rows, "fixtures": fixtures,
        "markets": market_names, "match_options": match_options,
    }


def load_odds_history(match_id, market):
    with sqlite3.connect(f"file:{DB_PATH.resolve()}?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            SELECT selection_name, odds, collected_at
            FROM odds_history
            WHERE match_id=? AND market_name=? AND odds>1 AND suspended=0
            ORDER BY collected_at
        """, (str(match_id), market)).fetchall()
    return pd.DataFrame(rows, columns=["Sélection", "Cote", "Collecté le"])


st.set_page_config(page_title="Betclic Intelligence V8.1", page_icon="📊",
                   layout="wide")
st.title("Betclic Bookmaker Intelligence V8.1")
st.caption("Tableau de bord local · base privée · consultation en lecture seule")

if st.sidebar.button("Actualiser les données"):
    st.cache_data.clear()

data = load_dashboard_data(str(DB_PATH), int(datetime.now().timestamp() // 60))
if "error" in data:
    st.error(data["error"])
    st.info("Restaurez la base privée dans data/betclic_history.db avant de lancer le tableau de bord.")
    st.stop()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Matchs suivis", f"{data['matches']:,}")
c2.metric("Enregistrements de cotes", f"{data['odds']:,}")
c3.metric("Matchs à venir", f"{data['upcoming']:,}")
c4.metric("Alertes enregistrées", f"{data['alerts']:,}")
c5.metric("Résultats confirmés", f"{data['confirmed']:,}")

tab1, tab2, tab3 = st.tabs(["Vue générale", "Analyse des cotes", "Pilote prédictif"])

with tab1:
    st.subheader("Prochains matchs suivis")
    fixtures = pd.DataFrame(data["fixtures"],
        columns=["ID", "Match", "Coup d'envoi", "Observations de cotes"])
    if fixtures.empty:
        st.info("Aucun match futur n'est actuellement présent dans la base.")
    else:
        st.dataframe(fixtures, use_container_width=True, hide_index=True)
    st.subheader("Alertes récentes")
    alerts = pd.DataFrame(data["alert_rows"], columns=[
        "Match", "Marché", "Sélection", "Type", "Direction", "Variation %", "Date UTC"
    ])
    if alerts.empty:
        st.info("Aucune alerte enregistrée pour le moment.")
    else:
        st.dataframe(alerts, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Historique des cotes")
    if not data["match_options"] or not data["markets"]:
        st.info("Les historiques de cotes ne sont pas encore disponibles.")
    else:
        labels = {f"{r[1]} · {r[0]}": r[0] for r in data["match_options"]}
        chosen = st.selectbox("Match", list(labels))
        market = st.selectbox("Marché", data["markets"])
        history = load_odds_history(labels[chosen], market)
        if history.empty:
            st.info("Aucune observation valide pour ce marché et ce match.")
        else:
            history["Collecté le"] = pd.to_datetime(history["Collecté le"], utc=True)
            chart = history.pivot_table(index="Collecté le", columns="Sélection",
                                        values="Cote", aggfunc="last")
            st.line_chart(chart)
            st.dataframe(history.sort_values("Collecté le", ascending=False),
                         use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Modèle de forme des équipes")
    st.warning(
        "Pilote expérimental uniquement. Les 15 observations admissibles sont "
        "utilisées pour l'entraînement, mais une classe ne compte qu'un seul "
        "exemple : la validation croisée et toute revendication de performance "
        "restent bloquées."
    )
    if PREDICTIONS_PATH.is_file():
        predictions = pd.read_csv(PREDICTIONS_PATH)
        st.metric("Prédictions futures publiées", len(predictions))
        if predictions.empty:
            st.info("Aucune affiche future ne satisfait actuellement les règles d'historique.")
        else:
            st.dataframe(predictions, use_container_width=True, hide_index=True)
    else:
        st.info(
            "Aucun CSV de prédictions local. Récupérez l'artefact "
            "betclic-model-and-reports du dernier workflow réussi."
        )
    st.caption(
        "Aucune mise automatique. Les probabilités expérimentales ne sont pas "
        "des conseils de pari et ne sont pas déclarées rentables."
    )
