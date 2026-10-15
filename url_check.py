"""Flag cited URLs that no agent successfully fetched in the same run.

Run with: uv run python url_check.py <run_file>
Exit code 1 if any cited URL is flagged, 0 otherwise.

fetched: every URL from a successful web_fetch in any thread of the run.
cited: every evidence source_url in the coordinator's final output.
URLs are compared as exact strings, with no normalization, because source_url
must be kept exactly as the fetch tool returned it.
"""

import json
import sys
from pathlib import Path

from run_output import get_final_output


def fetched_urls(run: dict) -> set[str]:
    urls = set()
    for entry in run["threads"]:
        events = entry["events"]
        ok = {e["tool_use_id"] for e in events if e["type"] == "agent.tool_result" and not e.get("is_error")}
        for e in events:
            if e["type"] == "agent.tool_use" and e["name"] == "web_fetch" and e["id"] in ok:
                urls.add(e["input"].get("url"))
    return urls


def check_run(run_file: str | Path) -> dict:
    """Return fetched URLs, cited URLs and flagged evidence (cited, never successfully fetched)."""
    run = json.loads(Path(run_file).read_text())
    fetched = fetched_urls(run)
    evidence = get_final_output(run_file)["evidence"]
    cited = {item.get("source_url") for item in evidence}
    flagged = [
        {"source_url": item.get("source_url"), "text": item.get("text")}
        for item in evidence
        if item.get("source_url") not in fetched
    ]
    return {"fetched": fetched, "cited": cited, "flagged": flagged}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: uv run python url_check.py <run_file>", file=sys.stderr)
        return 2
    result = check_run(sys.argv[1])
    print(f"fetched URLs: {len(result['fetched'])}")
    print(f"cited URLs: {len(result['cited'])}")
    if not result["flagged"]:
        print("flagged: none")
        return 0
    print(f"flagged: {len(result['flagged'])} evidence item(s) cite a URL that was never successfully fetched")
    for item in result["flagged"]:
        print(f"  {item['source_url']}\n    evidence text: {item['text']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
