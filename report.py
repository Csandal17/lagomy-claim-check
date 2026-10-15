"""Build the final report from a saved run: uv run python report.py <run_file>"""

import json
import re
import sys

from run_output import get_final_output

BANNED_PHRASES = (
    "is a myth",
    "has been debunked",
    "is debunked",
    "confirmed true",
    "confirmed false",
    "is true that",
    "is false that",
    "proven true",
    "proven false",
    "is a fact that",
    "fact-check:",
    "the verdict",
    "runs counter to",
    "run counter to",
    "contradicts the claim",
    "supports the claim",
    "refutes the claim",
    "corroborates the claim",
)

# Single words banned on word boundaries. true, false, confirmed and proven are left out on
# purpose: they appear in innocent prose ("unconfirmed", "not yet proven"), so only the phrases above catch them.
BANNED_WORDS = re.compile(r"\b(myth|debunked|verdict|fact-check)\b", re.IGNORECASE)

FALLBACK_SUMMARY = (
    "A summary could not be shown. See the verified and flagged statements and what the sources were silent on below."
)

LIMITATIONS = [
    "Only NHS and NICE sources were searched; BNF pages couldn't be fetched today.",
    "At most one follow-up round was run.",
    "This is not medical advice and makes no ruling on whether the claim is true.",
]


def phrase_guardrail(text: str) -> tuple[str, bool]:
    """Return (text, withheld). Case-insensitive substring match on banned multi-word phrases only."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in BANNED_PHRASES) or BANNED_WORDS.search(text):
        return FALLBACK_SUMMARY, True
    return text, False


def build_report(run_file: str) -> dict:
    out = get_final_output(run_file)
    verified, flagged = [], []
    for item in out["evidence"]:
        if item.get("supports_statement") == "yes" and item.get("addresses_the_claim") == "yes":
            verified.append(
                {"text": item["text"], "source_url": item["source_url"], "source_authority": item["source_authority"]}
            )
        else:
            flagged.append({"text": item["text"], "source_url": item["source_url"], "reason": item.get("reason")})
    summary_text, withheld = phrase_guardrail(out.get("summary") or "")
    if not out.get("summary"):
        summary_text, withheld = "", False  # old run from before step 5: nothing to guard
    return {
        "summary": {"text": summary_text, "withheld": withheld},
        "verified_statements": verified,
        "flagged_statements": flagged,
        "silent_on": out["not_found"],
        "limitations": LIMITATIONS,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: uv run python report.py <run_file>")
    print(json.dumps(build_report(sys.argv[1]), indent=2))
