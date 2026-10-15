"""Unit test for phrase_guardrail; no session needed: uv run python test_guardrail.py"""

from report import FALLBACK_SUMMARY, phrase_guardrail

text, withheld = phrase_guardrail("This is a myth, not a real risk.")
assert withheld and text == FALLBACK_SUMMARY, (text, withheld)

clean = "The NHS says there is little evidence for the benefit. Nothing was found on pregnancy."
text, withheld = phrase_guardrail(clean)
assert not withheld and text == clean, (text, withheld)

# bare single words must not trigger
text, withheld = phrase_guardrail("True to its label, the page lists a false start in the trial.")
assert not withheld, text

text, withheld = phrase_guardrail("THE VERDICT is in")
assert withheld

text, withheld = phrase_guardrail("Some people call it a myth.")
assert withheld, text
text, withheld = phrase_guardrail("Myth")
assert withheld

for ok in ("The claim is unconfirmed by the pages found.", "Safety with other medicines is not yet proven in these sources."):
    text, withheld = phrase_guardrail(ok)
    assert not withheld and text == ok, (text, withheld)

text, withheld = phrase_guardrail("This evidence runs counter to the claim.")
assert withheld, text
for ok in ("St John's wort can affect medicines that are available over-the-counter.", "Some medicines are available over the counter."):
    text, withheld = phrase_guardrail(ok)
    assert not withheld and text == ok, (text, withheld)

print("guardrail tests passed")
