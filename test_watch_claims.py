"""Live test of watch_claims.py (starts one real session, up to 300 cents).

Run with: uv run python test_watch_claims.py
Needs an empty claims/ (no log.json, no .txt files): it refuses to run over real data.
Expects: demo.txt done, demo_dup.txt moved to done/ as a duplicate, empty.txt errored with no session.
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).parent
CLAIM = "St John's wort is natural, so it's safe to take with other medicines."
key = hashlib.sha256(CLAIM.encode()).hexdigest()
log = ROOT / "claims/log.json"
leftovers = [p for d in ("inbox", "done", "errored", "reports") for p in (ROOT / "claims" / d).glob("*")]
if log.exists() or leftovers:
    raise SystemExit("claims/ is not empty; clear log.json and the four folders first")
for d in ("inbox", "done", "errored", "reports"):
    (ROOT / "claims" / d).mkdir(parents=True, exist_ok=True)
w = subprocess.Popen(["uv", "run", "python", "-u", "watch_claims.py"], cwd=ROOT)
try:
    time.sleep(2)
    (ROOT / "claims/inbox/demo.txt").write_text(CLAIM + "\n")
    t0 = time.time()
    while time.time() - t0 < 180:
        if log.exists() and json.loads(log.read_text()).get(key, {}).get("status") in ("done", "error"):
            break
        time.sleep(2)
    print(f"\n[test] demo finished after {time.time()-t0:.0f}s", flush=True)
    (ROOT / "claims/inbox/demo_dup.txt").write_text(CLAIM + "\n")
    (ROOT / "claims/inbox/empty.txt").write_text("  \n\t\n")
    t0 = time.time()
    while time.time() - t0 < 20 and any((ROOT / "claims/inbox").glob("*.txt")):
        time.sleep(1)
    time.sleep(1)
finally:
    w.send_signal(2)
    w.wait(timeout=30)
print("\n=== log.json ===")
print(log.read_text())
for d in ("inbox", "done", "errored", "reports"):
    print(d + "/:", sorted(p.name for p in (ROOT / "claims" / d).iterdir()))

entries = json.loads(log.read_text())
assert entries[key]["status"] == "done" and entries[key]["run_file"].startswith("runs/"), entries[key]
assert sorted(p.name for p in (ROOT / "claims/done").iterdir()) == ["demo.txt", "demo_dup.txt"]
assert [p.name for p in (ROOT / "claims/errored").iterdir()] == ["empty.txt"]
assert len(entries) == 2
print("\nOK")
