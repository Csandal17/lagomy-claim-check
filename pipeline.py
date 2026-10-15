"""Shared session runner: one coordinator session, one message, wait for idle, save the event log."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent
IDS_FILE = ROOT / "agent_ids.json"
RUNS_DIR = ROOT / "runs"

DEADLINE_SECONDS = 15 * 60


class PipelineError(Exception):
    """Something failed after the session was created, so it may already have spent budget."""

    def __init__(self, message: str, session_id: str):
        super().__init__(message)
        self.session_id = session_id


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_terminal(event) -> bool:
    if event.type == "session.status_terminated":
        return True
    return event.type == "session.status_idle" and event.stop_reason.type != "requires_action"


def wait_until_idle(client: anthropic.Anthropic, session_id: str, message: str):
    """Send the message and block until the session stops. Returns the stopping event."""
    seen: set[str] = set()
    sent = False
    deadline = time.monotonic() + DEADLINE_SECONDS
    while True:
        with client.beta.sessions.events.stream(session_id) as stream:
            if not sent:
                client.beta.sessions.events.send(
                    session_id,
                    events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
                )
                sent = True
            else:
                # Reconnected: SSE doesn't replay, so catch up from history first.
                for event in client.beta.sessions.events.list(session_id, order="asc"):
                    seen.add(event.id)
                    if is_terminal(event):
                        return event
            for event in stream:
                if event.id not in seen:
                    seen.add(event.id)
                    if event.type == "session.thread_created":
                        print(f"  thread created: {event.agent_name}")
                    elif event.type == "session.status_idle" and event.stop_reason.type == "requires_action":
                        print("  warning: session is waiting on requires_action (no tool should need approval)")
                if is_terminal(event):
                    return event
                if time.monotonic() > deadline:
                    print("  deadline passed - interrupting the session")
                    client.beta.sessions.events.send(session_id, events=[{"type": "user.interrupt"}])
                    deadline = float("inf")
        print("  stream closed before the session went idle - reconnecting")


def settle(client: anthropic.Anthropic, session_id: str):
    # The stream reports idle slightly before the session's stored status does.
    for _ in range(25):
        session = client.beta.sessions.retrieve(session_id)
        if session.status != "running":
            return session
        time.sleep(0.2)
    return session


def collect_threads(client: anthropic.Anthropic, session_id: str) -> list[dict]:
    threads = []
    for thread in client.beta.sessions.threads.list(session_id):
        events = [
            event.to_dict(mode="json")
            for event in client.beta.sessions.threads.events.list(thread.id, session_id=session_id)
        ]
        # This endpoint has no order parameter; ISO timestamps sort correctly as strings.
        events.sort(key=lambda event: event.get("processed_at") or "")
        threads.append({"thread": thread.to_dict(mode="json"), "events": events})
    return threads


def run_message_session(
    client: anthropic.Anthropic, label: str, agent_id: str, environment_id: str, budget_cents: str, message: str
) -> tuple[dict, Path]:
    """Create a session, send one message, wait for idle, save the event log. Returns (run, run file path)."""
    budget = {"type": "limit", "max_list_cost": {"amount": budget_cents, "currency": "USD"}}
    started_at = now_utc()
    session = client.beta.sessions.create(agent=agent_id, environment_id=environment_id, budget=budget)
    print(f"  session {session.id} (agent {agent_id})")
    try:
        stop_event = wait_until_idle(client, session.id, message)
        ended_at = now_utc()
        session = settle(client, session.id)
        threads = collect_threads(client, session.id)
        run = {
            "label": label,
            "session_id": session.id,
            "agent_id": agent_id,
            "message": message,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "stop_event": stop_event.to_dict(mode="json"),
            "session": session.to_dict(mode="json"),
            "threads": threads,
        }
        RUNS_DIR.mkdir(exist_ok=True)
        out = RUNS_DIR / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}_session_{label.lower()}.json"
        out.write_text(json.dumps(run, indent=2) + "\n")
    except Exception as err:
        raise PipelineError(f"{type(err).__name__}: {err}", session.id) from err
    return run, out


def run_claim_pipeline(claim_text: str, budget_cents: int | str = 300) -> str:
    """Run the coordinator on one claim and return the saved run file path.

    Raises ValueError for an empty claim before any session is created, and PipelineError if
    something fails after the session exists.
    """
    claim = claim_text.strip()
    if not claim:
        raise ValueError("claim text is empty")
    load_dotenv(ROOT / ".env")
    ids = json.loads(IDS_FILE.read_text())
    client = anthropic.Anthropic()
    _, out = run_message_session(
        client, "claim", ids["agents"]["coordinator"], ids["environment_id"], str(budget_cents), claim
    )
    return str(out)
