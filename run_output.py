"""Helpers for reading saved run files (runs/*.json written by checks.py)."""

import json
from pathlib import Path

REQUIRED_KEYS = {"parsed_claim", "evidence", "not_found"}


def text_of(content: list[dict] | None) -> str:
    return " ".join(block.get("text", "") for block in content or [] if block.get("type") == "text")


def get_final_output(run_file: str | Path) -> dict:
    """Return the coordinator's final JSON output from a saved run.

    The coordinator may send narration text before its JSON, so this takes the
    last primary-thread agent.message whose text parses as a bare JSON object
    with the keys parsed_claim, evidence and not_found. Raises ValueError,
    naming the run file, if none does.
    """
    return get_final_output_from_run(json.loads(Path(run_file).read_text()), run_file)


def get_final_output_from_run(run: dict, run_file: str | Path = "run") -> dict:
    """Same as get_final_output, for a run dict already in memory."""
    primary = [t for t in run["threads"] if t["thread"].get("parent_thread_id") is None]
    for thread in primary:
        for event in reversed(thread["events"]):
            if event["type"] != "agent.message":
                continue
            try:
                output = json.loads(text_of(event.get("content")).strip())
            except json.JSONDecodeError:
                continue
            if isinstance(output, dict) and REQUIRED_KEYS <= output.keys():
                return output
    raise ValueError(f"{run_file}: no primary-thread message parses as JSON with keys {sorted(REQUIRED_KEYS)}")
