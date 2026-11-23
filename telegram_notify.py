"""Send prediction summaries to Telegram after a successful collection run.

Credentials are read only from environment variables; never commit them.
"""
import csv
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PREDICTIONS_PATH = Path("reports/team_form_predictions.csv")
OUTCOMES = {"HOME": "1", "DRAW": "N", "AWAY": "2"}
LOCAL_TIMEZONE = "Africa/Douala"


def _pct(value):
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/d"


def format_kickoff(value, timezone_name=LOCAL_TIMEZONE):
    """Return the fixture date and time in Cameroon local time."""
    if not value:
        return "Date : non renseignée | Heure : non renseignée"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        try:
            local = stamp.astimezone(ZoneInfo(timezone_name))
            zone_label = "heure du Cameroun"
        except ZoneInfoNotFoundError:
            local = stamp.astimezone(timezone.utc)
            zone_label = "UTC (fuseau local indisponible)"
        return f"Date : {local:%d/%m/%Y} | Heure : {local:%H:%M} ({zone_label})"
    except (TypeError, ValueError, OverflowError):
        return "Date : invalide | Heure : invalide"


def _market_summary(row):
    fields = ("market_prob_home", "market_prob_draw", "market_prob_away")
    if not all(row.get(field) not in (None, "") for field in fields):
        return "Marché Betclic 1/N/2 : non disponible"
    return "Marché Betclic 1/N/2 : " + " / ".join(
        f"{label} {_pct(row.get(field))}"
        for label, field in zip(("1", "N", "2"), fields)
    )


def _news_lines(row):
    raw = row.get("web_research_findings_json", "")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return []
    lines = []
    for item in items[:2]:
        title = html.escape(str(item.get("title") or "Information consultée"))
        source = html.escape(str(item.get("source_name") or item.get("source") or "Source"))
        url = str(item.get("url") or "")
        if url.startswith(("https://", "http://")):
            lines.append(f'Info : <a href="{html.escape(url, quote=True)}">{title}</a> — {source}')
        else:
            lines.append(f"Info : {title} — {source}")
    return lines


def build_message(rows, run_url=""):
    lines = ["<b>BETCLIC V8.1 — Collecte et prédictions</b>"]
    lines.append(f"Matchs avec prédiction : <b>{len(rows)}</b>")
    if not rows:
        lines.append("Aucune prédiction exploitable pour cette collecte. Vérifier la disponibilité des matchs et des données.")
    for row in rows[:12]:
        competition = html.escape(row.get("competition_name") or "Championnat non renseigné")
        name = html.escape(row.get("match_name") or "Match sans nom")
        prediction = row.get("prediction", "")
        action = row.get("action_status", "NO_BET")
        probs = " / ".join(
            f"{label} {_pct(row.get(field))}"
            for label, field in (("1", "prob_home"), ("N", "prob_draw"), ("2", "prob_away"))
        )
        lines.extend([
            "",
            f"<b>Championnat : {competition}</b>",
            html.escape(format_kickoff(row.get("kickoff_utc"))),
            f"<b>{name}</b>",
            f"Pronostic modèle : <b>{OUTCOMES.get(prediction, html.escape(prediction or 'n/d'))}</b>",
            f"Probabilités modèle (1/N/2) : {probs}",
            _market_summary(row),
            f"Écart maximal modèle/marché : {_pct(float(row['max_abs_edge_pp']) / 100) if row.get('max_abs_edge_pp') not in (None, '') else 'n/d'}",
            f"Décision : <b>{html.escape(action)}</b>",
        ])
        if row.get("over_2_5") not in (None, ""):
            lines.append(
                f"Buts : Over 1,5 {_pct(row.get('over_1_5'))} | "
                f"Over 2,5 {_pct(row.get('over_2_5'))} | "
                f"Over 3,5 {_pct(row.get('over_3_5'))} | "
                f"BTTS Oui {_pct(row.get('btts_yes'))}"
            )
        movement = row.get("market_movement_status")
        if movement and movement != "NO_DATA":
            pattern = html.escape(row.get("market_movement_pattern") or "profil non précisé")
            lines.append(f"Mouvement des cotes : {html.escape(movement)} — {pattern}")
        research = row.get("web_research_status")
        if research and research != "NOT_TRIGGERED":
            lines.append(f"Recherche actualités : {html.escape(research)}")
            lines.extend(_news_lines(row))
    if len(rows) > 12:
        lines.append(f"… et {len(rows) - 12} autre(s) match(s) dans le rapport.")
    if run_url:
        lines.extend(["", f'<a href="{html.escape(run_url, quote=True)}">Ouvrir le rapport complet GitHub Actions</a>'])
    lines.append("")
    lines.append("<i>Modèle expérimental : probabilités non garanties. NO BET si données insuffisantes ou modèle non validé.</i>")
    message = "\n".join(lines)
    return message[:3900]


def read_predictions(path=PREDICTIONS_PATH):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def send_telegram(message, token, chat_id, opener=urlopen):
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    try:
        with opener(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Échec de l'envoi Telegram ({type(exc).__name__}).") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram a refusé le message.")
    return True


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("NOTIFICATION TELEGRAM IGNORÉE : secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID non configurés.")
        return 0
    rows = read_predictions()
    message = build_message(rows, os.getenv("GITHUB_RUN_URL", ""))
    send_telegram(message, token, chat_id)
    print(f"NOTIFICATION TELEGRAM ENVOYÉE : {len(rows)} prédiction(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
