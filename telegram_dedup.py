"""Idempotent Telegram notifications based on semantic prediction-table changes.

The public JSON state stores only normalized prediction fields, never credentials
or the private SQLite database. GitHub Actions serializes both notification
workflows through one repository-wide concurrency group.
"""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(os.getenv("TELEGRAM_STATE_PATH", "reports/telegram_prediction_state.json"))

# Fields that affect the prediction table/message. Generation timestamps,
# workflow IDs, CSV row order and training metadata are deliberately excluded.
NUMERIC_FIELDS = {
    "prob_home", "prob_draw", "prob_away",
    "market_prob_home", "market_prob_draw", "market_prob_away",
    "over_1_5", "over_2_5", "over_3_5", "btts_yes",
}
TEXT_FIELDS = (
    "competition_name", "match_name", "kickoff_utc",
    "prediction", "prediction_1x2", "action_status",
    "market_status", "comparison_status", "model_status",
    "history_quality", "goal_model_status",
    "market_movement_status", "market_movement_pattern",
)


def _text(value):
    return str(value or "").strip()


def _number(value):
    if value in (None, ""):
        return None
    try:
        # Telegram renders probabilities to one decimal percentage point.
        # Three decimal places in probability space avoid noise-only updates.
        return round(float(value), 3)
    except (TypeError, ValueError):
        return _text(value)


def _match_key(row):
    match_id = _text(row.get("match_id"))
    if match_id:
        return match_id
    return "|".join((
        _text(row.get("competition_name")),
        _text(row.get("match_name")),
        _text(row.get("kickoff_utc")),
    )).casefold()


def canonical_prediction_table(rows):
    """Return a stable semantic representation independent of row order/metadata."""
    canonical = []
    for row in rows:
        item = {"match_key": _match_key(row)}
        for field in TEXT_FIELDS:
            value = row.get(field)
            if field == "prediction":
                value = value or row.get("prediction_1x2")
            if field == "kickoff_utc":
                # Preserve the fixture instant while normalizing equivalent ISO forms.
                try:
                    from datetime import datetime
                    stamp = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
                    value = stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else stamp.isoformat()
                except (TypeError, ValueError, OverflowError):
                    value = _text(value)
            item[field] = _text(value)
        for field in NUMERIC_FIELDS:
            item[field] = _number(row.get(field))
        edge = row.get("max_abs_edge_pp")
        try:
            item["max_abs_edge_pp"] = round(float(edge), 1) if edge not in (None, "") else None
        except (TypeError, ValueError):
            item["max_abs_edge_pp"] = _text(edge)
        canonical.append(item)
    canonical.sort(key=lambda item: item["match_key"])
    return canonical


def _signature(canonical):
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_state(path=STATE_PATH):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "sources": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Fail closed: a damaged state must not cause an uncontrolled resend.
        raise RuntimeError(f"État anti-doublon Telegram illisible : {type(exc).__name__}.") from None
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("sources"), dict):
        raise RuntimeError("Format de l'état anti-doublon Telegram non reconnu; envoi bloqué par sécurité.")
    return state


def save_state(state, path=STATE_PATH, commit_to_git=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)

    if commit_to_git is None:
        commit_to_git = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    if commit_to_git:
        relative_path = path.as_posix()
        try:
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email",
                            "41898282+github-actions[bot]@users.noreply.github.com"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "--", relative_path],
                           check=True, capture_output=True, text=True)
            staged = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                    capture_output=True, text=True)
            if staged.returncode == 0:
                return
            if staged.returncode != 1:
                raise RuntimeError("Impossible de vérifier l'état Git indexé.")
            subprocess.run(["git", "commit", "-m", "Enregistrer l'état anti-doublon Telegram"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"],
                           check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            # Do not print command arguments or environment (which could contain credentials).
            detail = (exc.stderr or "").strip()[:240]
            suffix = f" Détail : {detail}" if detail else ""
            raise RuntimeError("Envoi effectué mais persistance Git de l'état Telegram échouée." + suffix) from None


def process_predictions(rows, token, chat_id, run_url, source_key,
                        message_builder, sender, state_path=STATE_PATH,
                        commit_to_git=None):
    """Send only first table or a semantic revision; persist only after API success."""
    if not rows:
        return {"status": "NO_VALID_PREDICTIONS", "sent": False}
    canonical = canonical_prediction_table(rows)
    signature = _signature(canonical)
    state = load_state(state_path)
    previous = state["sources"].get(source_key)
    if previous and previous.get("signature") == signature:
        return {"status": "UNCHANGED_SKIPPED", "sent": False, "matches": len(rows)}

    message = message_builder(rows, run_url, revision=bool(previous))
    # A failed Telegram call raises before the reference state is advanced.
    sender(message, token, chat_id)
    state["sources"][source_key] = {
        "signature": signature,
        "predictions": canonical,
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state, state_path, commit_to_git=commit_to_git)
    return {
        "status": "REVISION_SENT" if previous else "FIRST_TABLE_SENT",
        "sent": True,
        "matches": len(rows),
    }
