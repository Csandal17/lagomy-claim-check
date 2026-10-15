# claims/

Drop a claim as a `.txt` file into `inbox/` while `uv run python watch_claims.py` is running.

- `inbox/` — claims waiting to be checked. Files over the cap stay here untouched.
- `done/` — claims that were checked, plus duplicates of claims already in `log.json`.
- `errored/` — claims that failed (including empty files); the reason is in `log.json`.
- `reports/` — one report per checked claim, named `<sha256 of the stripped claim text>.json`.

`log.json` records every processed claim by that hash, so the same claim is never run twice.

Cap: each run of the script starts at most 5 new sessions (300 cents each, so at most 1500 cents).
When the cap is reached it leaves the remaining inbox files alone and stops; start it again for 5 more.
