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

PREDICTIONS_PATH = Path(os.getenv("PREDICTIONS_FILE", "reports/team_form_predictions.csv"))
OUTCOMES = {"HOME": "1", "DRAW": "N", "AWAY": "2"}
LOCAL_TIMEZONE = "Africa/Douala"


def _pct(value):
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/d"


def _decimal(value):
    try:
        return f"{float(value):.2f}"
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
    summary = "Marché Betclic 1/N/2 : " + " / ".join(
        f"{label} {_pct(row.get(field))}"
        for label, field in zip(("1", "N", "2"), fields)
    )
    age = row.get("market_snapshot_age_minutes")
    if age not in (None, ""):
        try:
            summary += f" | Snapshot : {float(age):.1f} min"
        except (TypeError, ValueError):
            pass
    return summary


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


def build_message(rows, run_url="", revision=False):
    """Build a valid Telegram HTML document without cutting through markup."""
    heading = "BETCLIC V8.1 — MISE À JOUR DES PRÉDICTIONS" if revision else "BETCLIC V8.1 — Collecte et prédictions"
    header = [f"<b>{heading}</b>", f"Matchs avec prédiction : <b>{len(rows)}</b>"]
    footer = ["", "<i>Probabilités indicatives, jamais garanties. NO BET si les données sont insuffisantes ou le modèle non validé.</i>"]
    if run_url:
        footer.insert(0, f'<a href="{html.escape(run_url, quote=True)}">Ouvrir le rapport complet GitHub Actions</a>')
    blocks = []
    for row in rows:
        competition = html.escape(str(row.get("competition_name") or "Championnat non renseigné")[:180])
        name = html.escape(str(row.get("match_name") or "Match sans nom")[:220])
        prediction = row.get("prediction") or row.get("prediction_1x2", "")
        action = row.get("action_status", "NO_BET")
        probs = " / ".join(
            f"{label} {_pct(row.get(field))}"
            for label, field in (("1", "prob_home"), ("N", "prob_draw"), ("2", "prob_away"))
        )
        block = [
            "",
            f"<b>Championnat : {competition}</b>",
            html.escape(format_kickoff(row.get("kickoff_utc"))),
            f"<b>{name}</b>",
            f"Pronostic modèle : <b>{OUTCOMES.get(prediction, html.escape(str(prediction or 'n/d')))}</b>",
            f"Probabilités modèle (1/N/2) : {probs}",
            _market_summary(row),
            f"Écart maximal modèle/marché : {_pct(float(row['max_abs_edge_pp']) / 100) if row.get('max_abs_edge_pp') not in (None, '') else 'n/d'}",
            f"Décision : <b>{html.escape(str(action)[:120])}</b>",
        ]
        if row.get("lambda_home") not in (None, "") and row.get("lambda_away") not in (None, ""):
            block.append(
                f"Buts attendus (modèle) : domicile {_decimal(row.get('lambda_home'))} | "
                f"extérieur {_decimal(row.get('lambda_away'))}"
            )
        status_parts = []
        for label, field in (
            ("modèle", "model_status"),
            ("validation", "validation_status"),
            ("calibration", "calibration_status"),
        ):
            if row.get(field):
                status_parts.append(f"{label} {html.escape(str(row[field])[:100])}")
        if status_parts:
            block.append("Statuts : " + " | ".join(status_parts))
        if row.get("history_quality"):
            block.append(f"Qualité historique : {html.escape(str(row['history_quality'])[:100])}")
        if row.get("over_2_5") not in (None, ""):
            block.append(
                f"Buts : Over 1,5 {_pct(row.get('over_1_5'))} | "
                f"Over 2,5 : {_pct(row.get('over_2_5'))} | "
                f"Over 3,5 {_pct(row.get('over_3_5'))} | "
                f"BTTS Oui {_pct(row.get('btts_yes'))}"
            )
        movement = row.get("market_movement_status")
        if movement and movement != "NO_DATA":
            pattern = html.escape(str(row.get("market_movement_pattern") or "profil non précisé")[:160])
            block.append(f"Mouvement des cotes : {html.escape(str(movement)[:100])} — {pattern}")
        research = row.get("web_research_status")
        if research and research != "NOT_TRIGGERED":
            block.append(f"Recherche actualités : {html.escape(str(research)[:100])}")
            block.extend(_news_lines(row))
        blocks.append("\n".join(block))

    # Telegram's message limit is 4096 characters. Include only complete fixture
    # blocks so a long report can never be cut in the middle of an HTML tag.
    selected = list(header)
    omitted = 0
    for index, block in enumerate(blocks):
        candidate = "\n".join(selected + [block] + footer)
        if len(candidate) <= 3900:
            selected.append(block)
        else:
            omitted = len(blocks) - index
            break
    if not rows:
        selected.append("Aucune prédiction exploitable pour cette collecte. Vérifier la disponibilité des matchs et des données.")
    if omitted:
        selected.append(f"… {omitted} match(s) omis du message pour respecter la limite Telegram; consulter le rapport complet.")
    message = "\n".join(selected + footer)
    if len(message) > 4096:
        # Defensive fallback: header/footer are static and always remain intact.
        raise RuntimeError("Message Telegram trop long après troncature sécurisée.")
    return message

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
    except HTTPError as exc:
        # Keep diagnostics useful without exposing the bot token or request URL.
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("description", "")
        except (ValueError, UnicodeDecodeError):
            detail = ""
        detail = str(detail)[:300]
        suffix = f" — {detail}" if detail else ""
        raise RuntimeError(f"Échec Telegram HTTP {exc.code}{suffix}.") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Échec de connexion Telegram ({type(exc).__name__}).") from None
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
    from telegram_dedup import process_predictions
    result = process_predictions(
        rows=rows,
        token=token,
        chat_id=chat_id,
        run_url=os.getenv("GITHUB_RUN_URL", ""),
        source_key=PREDICTIONS_PATH.name,
        message_builder=build_message,
        sender=send_telegram,
    )
    print("TELEGRAM :", result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
