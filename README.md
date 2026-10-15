# Lagomy Claim Check

Lagomy Labs prototype for Accel AI Innovate London, 15 October 2026.

Lagomy Claim Check takes a published supplement claim, such as "St John's wort is natural, so it's safe to take with other medicines", and checks it against UK sources. It splits the claim into separate assertions, finds passages on NHS and NICE pages that bear on each one, re-fetches every cited page to check the passage is really there and really about the claim, and returns a report: what the sources say, what was flagged, what they were silent on, and a short plain-language summary. It reports what individual sources say. It does not rule on whether the claim is true. Built on Claude Managed Agents (Python SDK, beta).

## How to run it

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), and an `ANTHROPIC_API_KEY` in `.env`.

**One-time setup.** `uv run python setup_agents.py` creates the environment and the three agents and writes their IDs to `agent_ids.json`. It refuses to run if that file exists, and the file in this repo belongs to the original author's account: to run your own copy, delete `agent_ids.json` first, then run setup. After that, never re-run it. Change an agent by editing `prompts/<agent>.md` and running `uv run python update_agents.py`, which updates the agents in place and re-pins the coordinator to its subagents' new versions.

Then pick an entry point:

- **From Python:** `pipeline.run_claim_pipeline(claim_text, budget_cents=300)` runs one session and returns the path of the saved event log under `runs/`. It raises `ValueError` on an empty claim before any session starts. Then `uv run python report.py <run_file>` prints the report JSON.
- **Named test sessions:** `uv run python checks.py handoff` (or `a`, `b`, `bnf`, `parse`, `personal`, `research`, `verify1`, `verify`, `handoff_test`). Each has a budget and prints a per-thread tool summary. `--summarize runs/<file>.json` re-prints a saved run for free.
- **Folder watcher:** `uv run python watch_claims.py` watches `claims/inbox/` for `.txt` files, skips claims it has already seen, writes reports to `claims/reports/`, and stops after 5 new sessions per run. See `claims/README.md`.
- **From a screenshot:** `uv run python screenshot_to_claim.py <image>` extracts the claim text, asks you to confirm or edit it, then runs the pipeline. `make_test_screenshot.py` makes a synthetic test image (needs Pillow: `uv run --with pillow make_test_screenshot.py out.png`).

Every run prints live status lines (searches, fetches, failures, threads finishing). Run logs go to `runs/` (gitignored). `url_check.py <run_file>` flags any cited URL that no agent actually fetched.

A finished example for the demo claim is in `examples/`: `st-johns-wort_run.json` (the full event log) and `st-johns-wort_report.json` (what `report.py` prints for it).

## Architecture in brief

- **Coordinator** parses the claim into labelled assertions, delegates, merges results, and writes the final JSON including a 2-4 sentence summary.
- **Researcher** (web_search + web_fetch, NHS and NICE domains only) finds passages and cites only text from pages it actually fetched, never search snippets.
- **Verifier** (web_fetch only) re-fetches each cited URL and checks that the passage is on the page (`supports_statement`) and speaks to the assertion (`addresses_the_claim`).
- If anything is unresolved, the coordinator sends the researcher exactly one follow-up in the same thread and the verifier the new items. The loop stops there.
- No agent has bash or file tools; each web tool is restricted with `allowed_domains`.

Two guardrails run in code, outside the agents:

1. **`report.phrase_guardrail`** checks the summary text for banned wording (verdict language such as "is a myth", "debunked", "the verdict", and phrases that rule on the evidence as a whole). On a match the summary is replaced with a fixed sentence and marked `withheld`.
2. **`url_check.py`** compares every cited `source_url` with the URLs an agent successfully fetched in that run and flags any that don't match, so an invented citation can't pass.

## What this deliberately doesn't do

- **No verdict on claims.** It never says a claim is true or false, and never says the evidence as a whole agrees or disagrees with it.
- **No personalised advice.** It doesn't say whether anyone should take something, and declines personal situations.
- **NHS and NICE only.** The BNF can't be fetched (`url_not_accessible`), so it is never cited. NICE fetches can return only part of a page.
- **At most one follow-up round.** Whatever is still unresolved after round 2 is reported as flagged or silent, not chased further.

## Known gaps

- The coordinator occasionally sends a stray message to a subagent (once, a bare "placeholder" to the researcher). It self-corrects, and the cap check counts real research rounds, not raw messages, so it doesn't read as a cap break. A prompt line aimed at it hasn't shown it again, but one clean run is not proof.
- Pressing Ctrl+C in `watch_claims.py` while a claim is running can leave that claim unlogged: its file stays in `inbox/`, so it would run again after a restart (a wasted session; the cap still bounds spend).
- The watcher's cap of 5 new sessions is untested under load; it is a plain counter that has only been exercised with one real session.
- The verifier is another model call, so its judgements are not guaranteed to be consistent between runs. Its reasons can use evaluative wording ("contradicts the assertion"); the phrase guardrail checks only the summary.
