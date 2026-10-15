"""Extract a supplement claim from a screenshot, confirm it, then run it through Claim Check.

Run with: uv run python screenshot_to_claim.py <image_path> [--assume-yes]
"""

import argparse
import base64
import mimetypes
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from pipeline import ROOT, run_claim_pipeline

MODEL = "claude-sonnet-5-5"
NO_CLAIM = "NO_CLAIM_FOUND"

INSTRUCTIONS = (
    "This is a screenshot of a social media post or web page. Extract only the specific factual claim "
    "about a supplement, ingredient or health topic, worded exactly as it appears. Never include a "
    "username, handle, timestamp, hashtag or engagement count (likes, shares, views, comments). "
    f"Reply with the claim text only. If there is no clear claim, reply with exactly {NO_CLAIM}."
)


def extract_claim_from_image(image_path: str | Path) -> str | None:
    """Return the claim text in the image, or None if the model finds no clear claim."""
    path = Path(image_path)
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise ValueError(f"unsupported image type: {path.suffix or path.name}")
    data = base64.standard_b64encode(path.read_bytes()).decode()
    load_dotenv(ROOT / ".env")
    response = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": INSTRUCTIONS},
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text or NO_CLAIM in text:
        return None
    return text


def confirm(claim: str) -> str | None:
    """Ask whether to run the claim. Returns the text to run, or None to stop."""
    while True:
        answer = input("Run Claim Check on this? [y]es / [n]o / [e]dit: ").strip().lower()
        if answer in ("y", "yes"):
            return claim
        if answer in ("n", "no"):
            return None
        if answer in ("e", "edit"):
            edited = input("Claim text: ").strip()
            if edited:
                return edited
            print("Empty claim, not changed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image_path")
    # --assume-yes skips the confirmation prompt. For testing only: a person should check what was extracted.
    parser.add_argument("--assume-yes", action="store_true", help="skip the confirmation prompt (testing only)")
    args = parser.parse_args()

    claim = extract_claim_from_image(args.image_path)
    if claim is None:
        sys.exit("No clear claim about a supplement, ingredient or health topic was found in the image.")
    print(f"Extracted claim:\n  {claim}\n")
    confirmed = claim if args.assume_yes else confirm(claim)
    if confirmed is None:
        sys.exit("Stopped; nothing was run.")
    run_file = run_claim_pipeline(confirmed)
    print(f"Saved {run_file}. Build the report with: uv run python report.py {run_file}")


if __name__ == "__main__":
    main()
