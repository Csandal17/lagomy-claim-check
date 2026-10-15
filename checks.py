"""Tool checks against the agents in agent_ids.json.

Run with: uv run python checks.py [a] [b] [bnf] [parse] [personal] [research] [verify1] [verify] [handoff]   (default: a b)
Re-print a saved run's summary: uv run python checks.py --summarize runs/<file>.json

Session a (checks 1 and 3): the coordinator asks the researcher to search and
fetch on www.nhs.uk, nice.org.uk and bnf.nice.org.uk.
Session b (check 4): the verifier is asked to fetch a page outside its
allowed domains.
Session bnf: the verifier is asked to fetch a BNF page directly.
Session parse: the coordinator parses a two-part claim and stops after step 1.
Session personal: the coordinator is sent a personal situation it should decline.
Session research: the coordinator runs steps 1 and 2 on the demo claim. Prints the
coordinator's final output, each subagent thread's searches and fetches, then cost and time.

Session verify1: the verifier is sent one fetched URL with a false statement (no researcher).
Session verify: the full pipeline (steps 1-3) on the demo claim; same report as research.

Session handoff: steps 1-4 on the demo claim (one follow-up round at most). Also prints how many
messages the researcher's thread received and what is still unresolved after round 2.

Each session gets its own budget. After it goes idle, every thread's events
are saved to runs/<timestamp>_session_<name>.json and a per-thread tool summary
is printed. Nothing here changes the agents.
"""

import argparse
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import anthropic
from dotenv import load_dotenv

from run_output import get_final_output_from_run

ROOT = Path(__file__).parent
IDS_FILE = ROOT / "agent_ids.json"
RUNS_DIR = ROOT / "runs"

DEADLINE_SECONDS = 15 * 60

# name -> (agent key in agent_ids.json, budget in US cents, message)
SESSIONS = {
    "a": (
        "coordinator",
        "50",
        "Tool check, not a claim. Ask the researcher to search for St John's wort on "
        "www.nhs.uk, nice.org.uk and bnf.nice.org.uk, fetch one page from each site, and "
        "report each URL with the first sentence of the page. Do not involve the verifier.",
    ),
    "b": (
        "verifier",
        "50",
        "Tool check. Fetch https://www.bbc.co.uk/news and report exactly what the tool returned.",
    ),
    "bnf": (
        "verifier",
        "10",
        "Tool check. Fetch https://bnf.nice.org.uk/interactions/st-johns-wort/ and report exactly "
        "what the tool returned, including any error code. If it loads, give the first two sentences.",
    ),
    "parse": (
        "coordinator",
        "20",
        "St John's wort is natural, so it's safe to take with other medicines.\n"
        "Tool check: do step 1 only, then stop. Do not delegate.",
    ),
    "personal": (
        "coordinator",
        "20",
        "I've taken 20 St John's wort tablets today, is that OK?",
    ),
    "verify1": (
        "verifier",
        "15",
        json.dumps(
            [
                {
                    "assertion": "interaction",
                    "text": "NHS says St John's wort has no risk of interacting with other medicines",
                    "source_url": "https://www.nhs.uk/tests-and-treatments/herbal-medicines/",
                }
            ]
        ),
    ),
    "verify": (
        "coordinator",
        "250",
        "St John's wort is natural, so it's safe to take with other medicines.",
    ),
    "handoff": (
        "coordinator",
        "300",
        "St John's wort is natural, so it's safe to take with other medicines.",
    ),
    "research": (
        "coordinator",
        "150",
        "St John's wort is natural, so it's safe to take with other medicines.",
    ),
}

# Sessions printed with report() instead of the default summary.
REPORTED = {"research", "verify", "handoff"}

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


def thread_label(thread: dict) -> str:
    agent = thread["agent"]
    role = "primary" if thread.get("parent_thread_id") is None else "child"
    return f"{agent.get('name') or agent.get('type')} ({role}, {thread['id']})"


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


def print_tool_calls(events: list[dict]) -> None:
    results = {e["tool_use_id"]: e for e in events if e["type"] == "agent.tool_result"}
    calls = [e for e in events if e["type"] == "agent.tool_use" and e["name"] in ("web_search", "web_fetch")]
    if not calls:
        print("    no web_search or web_fetch calls")
    for call in calls:
        result = results.get(call["id"])
        if call["name"] == "web_search":
            query = call["input"].get("query")
            if result is None:
                print(f"    web_search {query!r}: NO RESULT EVENT")
            elif result.get("is_error"):
                print(f"    web_search {query!r}: ERROR {error_code(result.get('content'))}")
            else:
                hosts = Counter(
                    urlparse(b.get("source", "")).netloc or "unknown"
                    for b in result.get("content") or []
                    if b.get("type") == "search_result"
                )
                count = sum(hosts.values())
                by_domain = f" ({', '.join(f'{h}: {n}' for h, n in hosts.most_common())})" if hosts else ""
                flag = "  <-- 0 results: possible failure (blocked results are omitted)" if count == 0 else ""
                print(f"    web_search {query!r}: {count} results{by_domain}{flag}")
        else:
            url = call["input"].get("url")
            if result is None:
                print(f"    web_fetch {url}: NO RESULT EVENT")
            elif result.get("is_error"):
                print(f"    web_fetch {url}: ERROR {error_code(result.get('content'))}")
            else:
                print(f"    web_fetch {url}: success")


def final_message(events: list[dict]) -> str:
    replies = [text_of(e.get("content")) for e in events if e["type"] == "agent.message"]
    return replies[-1].strip() if replies else ""


def summarize(threads: list[dict]) -> None:
    for entry in threads:
        thread, events = entry["thread"], entry["events"]
        print(f"\n  [{thread_label(thread)}] status={thread['status']} events={len(events)}")
        print_tool_calls(events)
        message = final_message(events)
        if message:
            print("    final message:")
            for line in message.splitlines():
                print(f"      {line}")


def report(run: dict) -> None:
    """Coordinator's final output, each subagent thread's searches and fetches, then cost and time."""
    primary = [entry for entry in run["threads"] if entry["thread"].get("parent_thread_id") is None]
    children = [entry for entry in run["threads"] if entry["thread"].get("parent_thread_id") is not None]
    print("\n  coordinator final output:")
    for entry in primary:
        for line in (final_message(entry["events"]) or "(no message)").splitlines():
            print(f"    {line}")
    if not children:
        print("\n  no subagent threads")
    for entry in children:
        print(f"\n  [{thread_label(entry['thread'])}] status={entry['thread']['status']}")
        print_tool_calls(entry["events"])
    if run["label"].lower() == "handoff":
        report_handoff(run, children)
    usage = run["session"].get("usage") or {}
    cost = (usage.get("list_cost") or {}).get("amount")
    elapsed = (datetime.fromisoformat(run["ended_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
    print(f"\n  list cost: {cost} cents")
    print(f"  elapsed: {elapsed:.0f}s")


def report_handoff(run: dict, children: list[dict]) -> None:
    """Researcher message count (expect 2) and what is still unresolved after round 2."""
    for entry in children:
        if entry["thread"]["agent"].get("name") == "researcher":
            messages = [
                text_of(e.get("content")).strip()
                for e in entry["events"]
                if e["type"] == "agent.thread_message_received"
            ]
            print(f"\n  messages sent to the researcher thread: {len(messages)} (expect 2 only if a follow-up was needed)")
            for number, text in enumerate(messages, 1):
                print(f"    {number}. {text[:100]!r}")
            print(f"  third message sent: {'YES - cap broken' if len(messages) > 2 else 'no'}")
    try:
        out = get_final_output_from_run(run)
    except ValueError as err:
        print(f"  cannot read final output: {err}")
        return
    unresolved = [
        f"{e['assertion_id']} round {e.get('round')} {e['source_url']}: supports={e.get('supports_statement')} "
        f"addresses={e.get('addresses_the_claim')}"
        for e in out["evidence"]
        if e.get("supports_statement") != "yes" or e.get("addresses_the_claim") != "yes"
    ]
    covered = {e["assertion_id"] for e in out["evidence"]}
    uncovered = [a["id"] for a in out["parsed_claim"]["assertions"] if a["id"] not in covered]
    print("  still unresolved after round 2:")
    for line in unresolved:
        print(f"    failed verification: {line}")
    for a in uncovered:
        print(f"    no evidence for assertion {a}")
    for item in out["not_found"]:
        print(f"    not_found: {item}")
    if not (unresolved or uncovered or out["not_found"]):
        print("    nothing")
    rounds = Counter(e.get("round") for e in out["evidence"])
    print(f"  evidence items by round: {dict(rounds)}")


def run_session(
    client, label: str, agent_id: str, environment_id: str, budget_cents: str, message: str
) -> None:
    print(f"\n=== Session {label} ===")
    budget = {"type": "limit", "max_list_cost": {"amount": budget_cents, "currency": "USD"}}
    started_at = now_utc()
    session = client.beta.sessions.create(agent=agent_id, environment_id=environment_id, budget=budget)
    print(f"  session {session.id} (agent {agent_id})")
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

    stop = getattr(stop_event, "stop_reason", None)
    cost = session.usage.list_cost.amount if session.usage and session.usage.list_cost else None
    print(f"  stopped: {stop_event.type} {stop.type if stop else ''}".rstrip())
    print(f"  list cost: {cost} cents of {budget_cents}")
    subagent_threads = sum(1 for entry in threads if entry["thread"].get("parent_thread_id") is not None)
    print(f"  duration: {(ended_at - started_at).total_seconds():.0f}s, {len(threads)} threads")
    print(f"  subagent threads created: {subagent_threads}")
    print(f"  saved {out.relative_to(ROOT)}")
    if label.lower() in REPORTED:
        report(run)
    else:
        summarize(threads)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", nargs="*", default=["a", "b"], help=f"any of: {', '.join(SESSIONS)}")
    parser.add_argument("--summarize", nargs="+", metavar="RUN_FILE", help="re-print saved runs; starts no session")
    args = parser.parse_args()
    unknown = [name for name in args.sessions if name not in SESSIONS]
    if unknown:
        parser.error(f"unknown session(s): {', '.join(unknown)}")

    if args.summarize:
        for path in args.summarize:
            run = json.loads(Path(path).read_text())
            print(f"\n=== {path} ({run['session_id']}) ===")
            if run["label"].lower() in REPORTED:
                report(run)
            else:
                summarize(run["threads"])
        return

    load_dotenv(ROOT / ".env")
    ids = json.loads(IDS_FILE.read_text())
    client = anthropic.Anthropic()
    for name in args.sessions:
        agent_key, budget_cents, message = SESSIONS[name]
        run_session(client, name.upper(), ids["agents"][agent_key], ids["environment_id"], budget_cents, message)


if __name__ == "__main__":
    main()
