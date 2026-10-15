"""Sync agent system prompts from prompts/*.md and repin the coordinator's subagents.

Run with: uv run python update_agents.py

For each agent in agent_ids.json, the system prompt in prompts/<name>.md is
compared with the agent's current system prompt and the agent is updated only
if they differ. The coordinator is then repinned to the researcher's and
verifier's current versions, because updating an agent doesn't change the
versions other agents have pinned. Updates pass the version that was read, so
a concurrent change fails with a 409 instead of being overwritten. The
resulting versions are saved to agent_ids.json.
"""

import json
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent
IDS_FILE = ROOT / "agent_ids.json"
PROMPTS_DIR = ROOT / "prompts"
SUBAGENTS = ("researcher", "verifier")


def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text().strip()


def pinned_versions(agent) -> dict[str, int]:
    multiagent = agent.multiagent.to_dict() if agent.multiagent else {}
    entries = (multiagent.get("subagents") or {}).get("predefined_agents") or []
    return {entry["id"]: entry.get("version") for entry in entries if entry.get("type") == "agent"}


def main() -> None:
    load_dotenv(ROOT / ".env")
    client = anthropic.Anthropic()
    ids = json.loads(IDS_FILE.read_text())
    agent_ids = ids["agents"]
    versions: dict[str, int] = {}

    for name in SUBAGENTS:
        agent = client.beta.agents.retrieve(agent_ids[name])
        prompt = read_prompt(name)
        if agent.system == prompt:
            print(f"{name}: prompt unchanged, version {agent.version}")
        else:
            agent = client.beta.agents.update(agent.id, version=agent.version, system=prompt)
            print(f"{name}: prompt updated, now version {agent.version}")
        versions[name] = agent.version

    coordinator = client.beta.agents.retrieve(agent_ids["coordinator"])
    changes: dict = {}
    reasons = []
    prompt = read_prompt("coordinator")
    if coordinator.system != prompt:
        changes["system"] = prompt
        reasons.append("prompt")
    wanted = {agent_ids[name]: versions[name] for name in SUBAGENTS}
    if pinned_versions(coordinator) != wanted:
        changes["multiagent"] = {
            "type": "multiagent_20261001",
            "workflows": {"type": "disabled"},
            "subagents": {
                "type": "enabled",
                "inline_agents": {"type": "disabled"},
                "predefined_agents": [
                    {"type": "agent", "id": agent_ids[name], "version": versions[name]} for name in SUBAGENTS
                ],
            },
        }
        reasons.append("subagent pins")
    if changes:
        coordinator = client.beta.agents.update(coordinator.id, version=coordinator.version, **changes)
        print(f"coordinator: updated {' and '.join(reasons)}, now version {coordinator.version}")
    else:
        print(f"coordinator: unchanged, version {coordinator.version}")
    versions["coordinator"] = coordinator.version

    pins = pinned_versions(coordinator)
    for name in SUBAGENTS:
        print(f"  coordinator pins {name} at version {pins.get(agent_ids[name])}")

    ids["versions"] = versions
    IDS_FILE.write_text(json.dumps(ids, indent=2) + "\n")
    print(f"Saved versions to {IDS_FILE.name}: {versions}")


if __name__ == "__main__":
    main()
