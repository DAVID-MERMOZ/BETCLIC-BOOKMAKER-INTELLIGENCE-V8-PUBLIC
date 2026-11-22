"""Predictions echantillon pour les affiches restantes du 22-11-2026."""
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
from baseline_market_evaluation import norm, parse_utc

DB = Path("data/betclic_history.db")
MODEL = Path("artifacts/team_form_model.joblib")
OUT = Path("reports/todays_sample_predictions.csv")
MIN_PRIOR = 3
N_LAST = 5

# Horaires UTC verifies sur les calendriers publies le 22 novembre 2026.
FIXTURES = [
    ("Liverpool", "Manchester United", "2026-11-22T16:30:00+00:00", "Premier League"),
    ("Werder Bremen", "Hamburger SV", "2026-11-22T16:30:00+00:00", "Bundesliga"),
    ("Lorient", "Rennes", "2026-11-22T16:15:00+00:00", "Ligue 1"),
    ("AC Milan", "Frosinone", "2026-11-22T17:00:00+00:00", "Serie A"),
    ("Osasuna", "Malaga", "2026-11-22T17:30:00+00:00", "La Liga"),
    ("Atalanta", "Inter", "2026-11-22T19:45:00+00:00", "Serie A"),
    ("Getafe", "Atletico Madrid", "2026-11-22T20:00:00+00:00", "La Liga"),
    ("Marseille", "Le Mans", "2026-11-22T19:45:00+00:00", "Ligue 1"),
]

ALIASES = {
    "liverpool": {"liverpool", "liverpool fc"},
    "manchester united": {"manchester united", "manchester united fc", "man utd", "manchester utd"},
    "werder bremen": {"werder bremen", "sv werder bremen"},
    "hamburger sv": {"hamburger sv", "hamburg", "hamburg sv"},
    "lorient": {"lorient", "fc lorient"},
    "rennes": {"rennes", "stade rennais", "stade rennais fc"},
    "ac milan": {"ac milan", "milan", "milan ac"},
    "frosinone": {"frosinone", "frosinone calcio"},
    "osasuna": {"osasuna", "ca osasuna"},
    "malaga": {"malaga", "malaga cf", "malaga cf."},
    "atalanta": {"atalanta", "atalanta bc"},
    "inter": {"inter", "inter milan", "internazionale", "fc internazionale"},
    "getafe": {"getafe", "getafe cf"},
    "atletico madrid": {"atletico madrid", "atletico", "atletico de madrid"},
    "marseille": {"marseille", "olympique marseille", "olympique de marseille"},
    "le mans": {"le mans", "le mans fc", "le mans union club 72"},
}

def form(history, club, kickoff):
    names = {norm(x) for x in ALIASES[club]}
    rows = [r for r in history if r["date"] < kickoff and
            (r["home"] in names or r["away"] in names)]
    rows.sort(key=lambda x: x["date"])
    rows = rows[-N_LAST:]
    if len(rows) < MIN_PRIOR:
        return None, len(rows), rows
    pts, gf, ga = [], [], []
    for r in rows:
        home = r["home"] in names
        scored, conceded = (r["hg"], r["ag"]) if home else (r["ag"], r["hg"])
        gf.append(scored); ga.append(conceded)
        pts.append(3 if scored > conceded else 1 if scored == conceded else 0)
    n = len(rows)
    return [sum(pts)/n, sum(gf)/n, sum(ga)/n, (sum(gf)-sum(ga))/n], n, rows

def main():
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as conn:
        raw = conn.execute("""
          SELECT utc_date, home_team, away_team, home_score, away_score
          FROM provider_results
          WHERE status='FINISHED' AND home_score IS NOT NULL
            AND away_score IS NOT NULL AND utc_date IS NOT NULL
        """).fetchall()
    history=[]
    for dt,h,a,hg,ag in raw:
        stamp=parse_utc(dt)
        if stamp and h and a:
            history.append({"date":stamp,"home":norm(h),"away":norm(a),"hg":int(hg),"ag":int(ag)})
    print("Historique fournisseur charge :",len(history))
    payload=joblib.load(MODEL)
    model=payload["model"]
    now=datetime.now(timezone.utc)
    rows=[]
    for home,away,kickoff_s,league in FIXTURES:
        kickoff=parse_utc(kickoff_s)
        if kickoff <= now:
            print("EXCLU coup d'envoi passe :",home,"-",away,kickoff_s)
            continue
        hf,hn,hr=form(history,home,kickoff)
        af,an,ar=form(history,away,kickoff)
        if hf is None or af is None:
            print("EXCLU historique insuffisant :",home,hn,"/",away,an)
            continue
        vector=hf+af
        probs={c:0.0 for c in ("HOME","DRAW","AWAY")}
        for label,p in zip(model.classes_,model.predict_proba([vector])[0]):
            probs[label]=float(p)
        pick=max(probs,key=probs.get)
        rows.append({"date_local":"2026-11-22","kickoff_utc":kickoff.isoformat(),
          "league":league,"home_team":home,"away_team":away,"prediction_1x2":pick,
          "prob_home":round(probs["HOME"],4),"prob_draw":round(probs["DRAW"],4),
          "prob_away":round(probs["AWAY"],4),"home_prior_n":hn,"away_prior_n":an,
          "model_status":"EXPERIMENTAL_15_MATCH_TRAINING_NO_CV",
          "note":"Prediction exploratoire, sans validation croisee ni cote Betclic integree."})
        print("PREDICTION :",home,"-",away,pick,probs,"historique",hn,an)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open("w",newline="",encoding="utf-8") as f:
        fields=list(rows[0]) if rows else ["date_local","kickoff_utc","league","home_team","away_team","prediction_1x2","prob_home","prob_draw","prob_away","home_prior_n","away_prior_n","model_status","note"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    print("TOTAL PREDICTIONS ECHANTILLON :",len(rows))
    print("CSV :",OUT)

if __name__=="__main__":
    main()
