# Lagomy Claim Check

Prototype that checks a published supplement claim against UK sources, built on Claude Managed Agents (Python SDK beta, `uv run python ...`). A coordinator parses the claim into labelled assertions and delegates to a researcher (web_search + web_fetch), whose citations a verifier (web_fetch only) re-fetches and checks. The coordinator sends the verifier one list of `{assertion, text, source_url}` (the assertion text looked up from `parsed_claim` by `assertion_id`), so `addresses_the_claim` is judged against the real assertion, then merges the verifier's fields into each evidence item. If anything is unresolved (an assertion with no evidence, or an item the verifier didn't fully support), the coordinator sends the researcher exactly one follow-up in the same thread and the verifier the new items; the handoff loop is capped at one round. Evidence items carry a `round` field (1 or 2), and the researcher's round 2 reply holds only new items plus an updated `not_found`. It never gives advice or says a claim is true or false.

## Files
- `setup_agents.py` — one-time creation of the environment and the three agents. Refuses to run if `agent_ids.json` exists; never re-run to change agents.
- `agent_ids.json` — environment ID, agent IDs, NHS domain in use, current agent versions.
- `prompts/{researcher,verifier,coordinator}.md` — the agents' system prompts (source of truth).
- `update_agents.py` — applies changed prompts to the agents, then re-pins the coordinator's subagents to their new versions and saves versions to `agent_ids.json`.
- `checks.py` — named test sessions (`a`, `b`, `bnf`, `parse`, `personal`, `research`, `verify1`, `verify`, `handoff`), each with a budget; saves every thread's events to `runs/` and prints a tool summary. `--summarize runs/<file>.json` re-prints a saved run without starting a session.
- `run_output.py` — `get_final_output(run_file)`: the coordinator's final JSON from a saved run.
- `url_check.py` — `uv run python url_check.py <run_file>` flags cited `source_url`s that no agent successfully fetched in the run; exit 1 if any. `test_planted_citation.py` plants a fake citation and confirms it is flagged.
- `runs/` — session event logs, gitignored.

## Rules
- Use `www.nhs.uk`, not `nhs.uk`: the API rejects `nhs.uk` as a public suffix in `allowed_domains`.
- Sources are www.nhs.uk and nice.org.uk only. bnf.nice.org.uk pages can't be fetched (`url_not_accessible`), so never cite BNF.
- Agents cite only text from pages they actually fetched, never search snippets. NICE fetches can return only part of a page.
- Change prompts by editing `prompts/` and running `update_agents.py`. Updating an agent doesn't move the coordinator's pinned subagent versions; the script does that.
- No agent gets bash or file tools: every toolset uses `default_config: {"enabled": false}` and turns on only the web tools it needs, each restricted with `allowed_domains`.
- Update agents in place by ID; don't create new ones.
- Give every test session a budget (`max_list_cost` in whole US cents as a string).
- Keep each evidence `source_url` exactly as returned by the fetch, even across a redirect or canonical change. Never substitute the redirect target or canonical URL; it must match a URL the agent actually fetched.
- Any code reading the coordinator's output must use `get_final_output`, never the last message directly: the coordinator may send narration text before its JSON.
