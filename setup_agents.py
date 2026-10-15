"""One-time setup: create the claim-check environment and its three agents.

Run with: uv run python setup_agents.py

Writes agent_ids.json. If that file already exists, nothing is created: later
prompt or tool changes should update these agents by ID, not create new ones.
"""

import json
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

IDS_FILE = Path(__file__).parent / "agent_ids.json"

MODEL = "claude-sonnet-5-5"
ENVIRONMENT_NAME = "claim-check"
NICE_DOMAIN = "nice.org.uk"
NHS_DOMAIN = "nhs.uk"
NHS_FALLBACK_DOMAIN = "www.nhs.uk"
WEB_FETCH_MAX_CONTENT_TOKENS = 20000

RESEARCHER_SYSTEM = (
    "You research one supplement claim using only NHS, NICE and BNF pages. "
    "Return evidence statements, each with the exact source_url you fetched it from. "
    "Note what you looked for and couldn't find. "
    "Never give advice or say whether the claim is true."
)
VERIFIER_SYSTEM = (
    "You check citations. For each statement and URL you are given, fetch that exact URL "
    "and report url_was_retrieved, supports_statement, addresses_the_claim, and a one-line reason. "
    "Check every citation, not a sample. If a page won't load, mark it unverified with the reason. "
    "Never guess what a page says, and never fetch other pages to fill a gap."
)
COORDINATOR_SYSTEM = (
    "You run the claim-check process. You never research or verify anything yourself. "
    "Send the claim to the agent named researcher, then pass its statements and URLs "
    "to the agent named verifier exactly as written."
)


def toolset(web_tools: list[str], nhs_domain: str) -> list[dict]:
    """agent_toolset_20260401 with everything off except the named web tools."""
    configs = []
    for name in web_tools:
        config = {
            "name": name,
            "enabled": True,
            "allowed_domains": [nhs_domain, NICE_DOMAIN],
        }
        if name == "web_fetch":
            config["max_content_tokens"] = WEB_FETCH_MAX_CONTENT_TOKENS
        configs.append(config)
    return [
        {
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": configs,
        }
    ]


def is_nhs_domain_error(error: anthropic.BadRequestError) -> bool:
    # The NHS domain is first in allowed_domains, so a rejection names it or index 0.
    text = str(error)
    return NHS_DOMAIN in text or "allowed_domains.0" in text


def main() -> None:
    if IDS_FILE.exists():
        print(f"{IDS_FILE.name} already exists - nothing created.")
        print("Update the existing agents by ID instead of re-running setup.")
        return

    load_dotenv()
    client = anthropic.Anthropic()
    created: dict[str, str] = {}

    try:
        environment = client.beta.environments.create(name=ENVIRONMENT_NAME)
        created["environment"] = environment.id
        print(f"Environment {ENVIRONMENT_NAME}: {environment.id}")

        nhs_domain = NHS_DOMAIN
        try:
            researcher = client.beta.agents.create(
                name="researcher",
                model=MODEL,
                system=RESEARCHER_SYSTEM,
                tools=toolset(["web_search", "web_fetch"], nhs_domain),
            )
        except anthropic.BadRequestError as error:
            if not is_nhs_domain_error(error):
                raise
            print(f"Researcher rejected {nhs_domain!r}: {error}")
            nhs_domain = NHS_FALLBACK_DOMAIN
            print(f"Retrying every agent with {nhs_domain!r}.")
            researcher = client.beta.agents.create(
                name="researcher",
                model=MODEL,
                system=RESEARCHER_SYSTEM,
                tools=toolset(["web_search", "web_fetch"], nhs_domain),
            )
        created["researcher"] = researcher.id
        print(f"Agent researcher: {researcher.id}")

        verifier = client.beta.agents.create(
            name="verifier",
            model=MODEL,
            system=VERIFIER_SYSTEM,
            tools=toolset(["web_fetch"], nhs_domain),
        )
        created["verifier"] = verifier.id
        print(f"Agent verifier: {verifier.id}")

        coordinator = client.beta.agents.create(
            name="coordinator",
            model=MODEL,
            system=COORDINATOR_SYSTEM,
            tools=toolset([], nhs_domain),
            multiagent={
                "type": "multiagent_20261001",
                "workflows": {"type": "disabled"},
                "subagents": {
                    "type": "enabled",
                    "inline_agents": {"type": "disabled"},
                    "predefined_agents": [
                        {"type": "agent", "id": researcher.id},
                        {"type": "agent", "id": verifier.id},
                    ],
                },
            },
        )
        created["coordinator"] = coordinator.id
        print(f"Agent coordinator: {coordinator.id}")
    except anthropic.APIError:
        if created:
            print("Setup failed after creating:", json.dumps(created, indent=2), file=sys.stderr)
            print("Clean these up (or record them) before re-running.", file=sys.stderr)
        raise

    ids = {
        "environment_id": environment.id,
        "agents": {
            "researcher": researcher.id,
            "verifier": verifier.id,
            "coordinator": coordinator.id,
        },
        "nhs_domain": nhs_domain,
    }
    IDS_FILE.write_text(json.dumps(ids, indent=2) + "\n")
    print(f"NHS domain used: {nhs_domain}")
    print(f"Saved {IDS_FILE.name}")


if __name__ == "__main__":
    main()
