"""Plant a never-fetched citation in a saved run and confirm url_check.py flags exactly it.

Run with: uv run python test_planted_citation.py
"""

import json
import subprocess
import sys
from pathlib import Path

from run_output import get_final_output, text_of

SOURCE = Path("runs/20261015T092438Z_session_research.json")
PLANTED = Path("runs/planted_never_fetched.json")
FAKE_URL = "https://www.nhs.uk/conditions/st-johns-wort-and-warfarin-interaction/"
FAKE_TEXT = "Taking St John's wort with warfarin can change how well warfarin works."

run = json.loads(SOURCE.read_text())
output = get_final_output(SOURCE)
output["evidence"].append(
    {"assertion_id": "a2", "text": FAKE_TEXT, "source_url": FAKE_URL, "source_authority": "NHS"}
)
primary = next(t for t in run["threads"] if t["thread"].get("parent_thread_id") is None)
message = next(
    e for e in reversed(primary["events"]) if e["type"] == "agent.message" and text_of(e["content"]).strip().startswith('{"parsed_claim"')
)
message["content"] = [{"type": "text", "text": json.dumps(output)}]
PLANTED.write_text(json.dumps(run, indent=2) + "\n")

proc = subprocess.run([sys.executable, "url_check.py", str(PLANTED)], capture_output=True, text=True)
print(proc.stdout, end="")
flagged = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("https://")]
assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}"
assert flagged == [FAKE_URL], f"expected exactly the planted URL, got {flagged}"
print("OK: exactly the planted URL was flagged, exit code 1")
