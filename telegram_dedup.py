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
EXPECTED_GOAL_FIELDS = {"lambda_home", "lambda_away"}
TEXT_FIELDS = (
    "competition_name", "match_name", "kickoff_utc",
    "prediction", "prediction_1x2", "action_status",
    "market_status", "comparison_status", "model_status",
    "validation_status", "calibration_status",
    "history_quality", "goal_model_status",
)


def _text(value):
    return str(value or "").strip()


def _number(value, digits=3):
    if value in (None, ""):
        return None
    try:
        # Probabilities are displayed to 0.1 percentage point; expected goals
        # are displayed to 0.01 goal. Match the signature to visible precision.
        return round(float(value), digits)
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
        for field in EXPECTED_GOAL_FIELDS:
            item[field] = _number(row.get(field), digits=2)
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


def load_state(path=STATE_PATH, commit_to_git=None):
    """Load dedup state and migrate legacy raw rows to a digest-only format."""
    path = Path(path)
    if not path.exists():
        return {"version": 2, "sources": {}, "pending": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Fail closed: a damaged state must not cause an uncontrolled resend.
        raise RuntimeError(f"État anti-doublon Telegram illisible : {type(exc).__name__}.") from None
    if not isinstance(state, dict) or not isinstance(state.get("sources"), dict):
        raise RuntimeError("Format de l'état anti-doublon Telegram non reconnu; envoi bloqué par sécurité.")
    if state.get("version") not in (1, 2):
        raise RuntimeError("Version de l'état anti-doublon Telegram non reconnue; envoi bloqué par sécurité.")

    # The public repository needs only signatures to compare tables. Do not
    # persist fixture names, provider IDs, probabilities, scores or row payloads.
    safe_sources = {}
    for source_key, entry in state["sources"].items():
        if not isinstance(entry, dict) or not entry.get("signature"):
            raise RuntimeError("Entrée de l'état anti-doublon Telegram invalide; envoi bloqué par sécurité.")
        predictions = entry.get("predictions")
        if isinstance(predictions, list):
            match_count = len(predictions)
        else:
            try:
                match_count = max(0, int(entry.get("match_count", 0)))
            except (TypeError, ValueError, OverflowError):
                match_count = 0
        safe_sources[str(source_key)] = {
            "signature": str(entry["signature"]),
            "match_count": match_count,
            "sent_at_utc": str(entry.get("sent_at_utc") or ""),
        }

    raw_pending = state.get("pending", {})
    if not isinstance(raw_pending, dict):
        raise RuntimeError("État pending Telegram invalide; envoi bloqué par sécurité.")
    safe_pending = {}
    for source_key, entry in raw_pending.items():
        if not isinstance(entry, dict) or not entry.get("signature"):
            raise RuntimeError("Entrée pending Telegram invalide; envoi bloqué par sécurité.")
        sent_parts = entry.get("sent_parts", [])
        if not isinstance(sent_parts, list) or any(
            not isinstance(index, int) or index < 0 for index in sent_parts
        ):
            raise RuntimeError("Progression pending Telegram invalide; envoi bloqué par sécurité.")
        try:
            total_parts = max(0, int(entry.get("total_parts", 0)))
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError("Nombre de messages pending Telegram invalide; envoi bloqué par sécurité.") from None
        safe_pending[str(source_key)] = {
            "signature": str(entry["signature"]),
            "sent_parts": sorted(set(sent_parts)),
            "total_parts": total_parts,
        }

    normalized = {"version": 2, "sources": safe_sources, "pending": safe_pending}
    if normalized != state:
        # A v1 state may contain complete raw prediction rows. Replace it with
        # the digest-only representation while preserving the sent signature.
        save_state(normalized, path, commit_to_git=commit_to_git)
    return normalized


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
            subprocess.run(["git", "add", "-f", "--", relative_path],
                           check=True, capture_output=True, text=True)
            staged = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                    capture_output=True, text=True)
            if staged.returncode == 0:
                return
            if staged.returncode != 1:
                raise RuntimeError("Impossible de vérifier l'état Git indexé.")
            subprocess.run(["git", "commit", "-m", "Enregistrer l'état anti-doublon Telegram"],
                           check=True, capture_output=True, text=True)

            # Another repository update can land after checkout and before push.
            # Fetch/rebase and retry a bounded number of times instead of failing
            # immediately with a non-fast-forward rejection.
            last_error = ""
            for attempt in range(3):
                pushed = subprocess.run(
                    ["git", "push", "origin", "HEAD:main"],
                    capture_output=True, text=True,
                )
                if pushed.returncode == 0:
                    return
                last_error = (pushed.stderr or pushed.stdout or "").strip()[:180]

                fetched = subprocess.run(
                    ["git", "fetch", "origin", "main"],
                    capture_output=True, text=True,
                )
                if fetched.returncode != 0:
                    last_error = (fetched.stderr or fetched.stdout or last_error).strip()[:180]
                    continue

                rebased = subprocess.run(
                    ["git", "rebase", "origin/main"],
                    capture_output=True, text=True,
                )
                if rebased.returncode != 0:
                    subprocess.run(["git", "rebase", "--abort"],
                                   capture_output=True, text=True)
                    last_error = (rebased.stderr or rebased.stdout or "conflit pendant le rebase").strip()[:180]
                    break

            suffix = f" Détail : {last_error}" if last_error else ""
            raise RuntimeError(
                "Envoi effectué mais persistance Git de l'état Telegram échouée après 3 tentatives." + suffix
            )
        except subprocess.CalledProcessError as exc:
            # Do not print command arguments or environment (which could contain credentials).
            detail = (exc.stderr or "").strip()[:180]
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
    state = load_state(state_path, commit_to_git=commit_to_git)
    previous = state["sources"].get(source_key)
    pending = state.get("pending", {}).get(source_key)
    if previous and previous.get("signature") == signature and not pending:
        return {"status": "UNCHANGED_SKIPPED", "sent": False, "matches": len(rows)}

    messages = message_builder(rows, run_url, revision=bool(previous))
    if isinstance(messages, str):
        messages = [messages]
    if pending and pending.get("signature") == signature:
        sent_parts = set(pending.get("sent_parts", []))
    else:
        sent_parts = set()
        state.setdefault("pending", {})[source_key] = {
            "signature": signature, "sent_parts": [], "total_parts": len(messages)
        }
        save_state(state, state_path, commit_to_git=commit_to_git)

    for index, message in enumerate(messages):
        if index in sent_parts:
            continue
        sender(message, token, chat_id)
        sent_parts.add(index)
        state["pending"][source_key]["sent_parts"] = sorted(sent_parts)
        save_state(state, state_path, commit_to_git=commit_to_git)

    state["sources"][source_key] = {
        "signature": signature, "match_count": len(rows),
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    state["pending"].pop(source_key, None)
    save_state(state, state_path, commit_to_git=commit_to_git)
    return {
        "status": "REVISION_SENT" if previous else "FIRST_TABLE_SENT",
        "sent": True, "matches": len(rows), "messages": len(messages),
    }
