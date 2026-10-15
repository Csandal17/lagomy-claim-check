"""Shared session runner: one coordinator session, one message, wait for idle, save the event log."""

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent
IDS_FILE = ROOT / "agent_ids.json"
RUNS_DIR = ROOT / "runs"

DEADLINE_SECONDS = 15 * 60
STATUS_POLL_SECONDS = 2

KNOWN_ERROR_CODES = (
    "url_not_allowed",
    "url_not_accessible",
    "url_too_long",
    "unsupported_content_type",
    "too_many_requests",
    "max_uses_exceeded",
    "invalid_input",
    "unavailable",
)


class PipelineError(Exception):
    """Something failed after the session was created, so it may already have spent budget."""

    def __init__(self, message: str, session_id: str):
        super().__init__(message)
        self.session_id = session_id


class StatusPrinter:
    """Prints one line per new search, fetch, failure or finished subagent thread while a session runs.

    Read-only: it lists threads and their events from a background thread and never touches the
    saved run, so the run file is the same with or without it.
    """

    def __init__(self, client: anthropic.Anthropic, session_id: str):
        self.client, self.session_id = client, session_id
        self.threads_seen: set[str] = set()
        self.events_seen: dict[str, set[str]] = {}
        self.tool_names: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)
        self.poll()  # flush anything that arrived after the last poll

    def _run(self) -> None:
        while not self._stop.wait(STATUS_POLL_SECONDS):
            self.poll()

    def poll(self) -> None:
        try:
            for thread in self.client.beta.sessions.threads.list(self.session_id):
                name = thread.agent.name or thread.agent.type
                primary = thread.parent_thread_id is None
                if thread.id not in self.threads_seen:
                    self.threads_seen.add(thread.id)
                    if not primary:
                        print(f"  {name} thread started", flush=True)
                events = [
                    e.to_dict(mode="json")
                    for e in self.client.beta.sessions.threads.events.list(thread.id, session_id=self.session_id)
                ]
                events.sort(key=lambda e: e.get("processed_at") or "")
                seen = self.events_seen.setdefault(thread.id, set())
                for event in events:
                    if event["id"] in seen:
                        continue
                    seen.add(event["id"])
                    line = self._line(name, primary, event)
                    if line:
                        print(f"  {line}", flush=True)
        except Exception as err:  # status lines are best-effort; never break the run
            print(f"  (status lines paused: {type(err).__name__})", flush=True)

    def _line(self, name: str, primary: bool, event: dict) -> str | None:
        kind = event["type"]
        if kind == "agent.tool_use":
            self.tool_names[event["id"]] = event["name"]
            if event["name"] == "web_search":
                return f"{name}: searching {event['input'].get('query')}"
            if event["name"] == "web_fetch":
                return f"{name}: fetching {event['input'].get('url')}"
        elif kind == "agent.tool_result" and event.get("is_error"):
            tool = self.tool_names.get(event.get("tool_use_id"), "tool")
            return f"{name}: {tool} failed ({error_code(event.get('content'))})"
        elif kind in ("session.thread_status_idle", "session.thread_status_terminated") and not primary:
            return f"{name} thread finished"
        return None


def text_of(content: list[dict] | None) -> str:
    return " ".join(block.get("text", "") for block in content or [] if block.get("type") == "text")


def error_code(content: list[dict] | None) -> str:
    text = text_of(content)
    match = re.search(r'error_code"?\s*[:=]\s*"?([a-z_]+)', text)
    if match:
        return match.group(1)
    for code in KNOWN_ERROR_CODES:
        if code in text:
            return code
    return f"unrecognised error: {text[:160]!r}"


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
                    if event.type == "session.status_idle" and event.stop_reason.type == "requires_action":
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
    printer = StatusPrinter(client, session.id)
    printer.start()
    try:
        try:
            stop_event = wait_until_idle(client, session.id, message)
        finally:
            printer.stop()
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
