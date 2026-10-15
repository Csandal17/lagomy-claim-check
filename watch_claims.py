"""Watch claims/inbox/ for .txt claims and run each through the pipeline.

Run with: uv run python watch_claims.py
At most CAP new sessions per run of this script. See claims/README.md.
"""

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline import PipelineError, run_claim_pipeline
from report import build_report

ROOT = Path(__file__).parent
CLAIMS = ROOT / "claims"
INBOX, DONE, ERRORED, REPORTS = (CLAIMS / n for n in ("inbox", "done", "errored", "reports"))
LOG_FILE = CLAIMS / "log.json"

CAP = 5
POLL_SECONDS = 5
BUDGET_CENTS = 300


def load_log() -> dict:
    return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else {}


def save_log(log: dict) -> None:
    """Write to a temp file then rename, so an interrupt mid-write can't corrupt the log."""
    tmp = LOG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(log, indent=2) + "\n")
    os.replace(tmp, LOG_FILE)


def move(path: Path, folder: Path) -> None:
    target = folder / path.name
    if target.exists():
        target = folder / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), target)


def claim_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def one_line(report: dict) -> str:
    return (
        f"{len(report['verified_statements'])} verified, {len(report['flagged_statements'])} flagged, "
        f"{len(report['silent_on'])} silent"
    )


def process(path: Path, log: dict, new_sessions: int) -> tuple[int, bool]:
    """Handle one file. Returns (updated new-session counter, whether the cap stopped us)."""
    text = path.read_text()
    key = claim_hash(text)
    if key in log:
        move(path, DONE)
        print(f"duplicate: {path.name} matches {key[:10]} ({log[key]['status']}); moved to done/")
        return new_sessions, False
    if new_sessions >= CAP:
        print(f"cap of {CAP} new sessions reached; leaving {path.name} in inbox/")
        return new_sessions, True

    entry = {
        "status": "error",
        "claim_text": text.strip(),
        "run_file": None,
        "report_file": None,
        "error": None,
        "processed_at": None,
    }
    destination = ERRORED
    try:
        print(f"processing {path.name} ({key[:10]})...")
        entry["run_file"] = str(Path(run_claim_pipeline(text, BUDGET_CENTS)).relative_to(ROOT))
        new_sessions += 1  # the session exists and its budget is spent
        report = build_report(str(ROOT / entry["run_file"]))
        report_file = REPORTS / f"{key}.json"
        report_file.write_text(json.dumps(report, indent=2) + "\n")
        entry.update(status="done", report_file=str(report_file.relative_to(ROOT)))
        destination = DONE
        print(f"done: {path.name}: {one_line(report)}")
    except Exception as err:
        if isinstance(err, PipelineError):
            new_sessions += 1  # failed after the session was created
        entry["error"] = f"{type(err).__name__}: {err}"
        print(f"error: {path.name}: {entry['error']}")
    entry["processed_at"] = now()
    log[key] = entry
    save_log(log)
    move(path, destination)
    return new_sessions, False


def main() -> None:
    for folder in (INBOX, DONE, ERRORED, REPORTS):
        folder.mkdir(parents=True, exist_ok=True)
    log = load_log()
    new_sessions = 0
    handled = 0
    print(f"watching {INBOX.relative_to(ROOT)}/ every {POLL_SECONDS}s (cap {CAP} new sessions). Ctrl+C to stop.")
    try:
        capped = False
        while not capped:
            for path in sorted(INBOX.glob("*.txt")):
                new_sessions, capped = process(path, log, new_sessions)
                handled += 1
                if capped:
                    break
            if not capped:
                time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\ninterrupted")
    statuses = [e["status"] for e in log.values()]
    print(
        f"summary: {new_sessions} new session(s) this run, {handled} file(s) handled; "
        f"log has {statuses.count('done')} done, {statuses.count('error')} error"
    )


if __name__ == "__main__":
    main()
