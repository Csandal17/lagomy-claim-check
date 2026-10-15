"""Make a synthetic social-post screenshot for testing screenshot_to_claim.py.

Pillow is a dev-only dependency, not needed to run screenshot_to_claim.py itself:
  uv run --with pillow make_test_screenshot.py <out.png>
"""

import sys
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (900, 500), "white")
d = ImageDraw.Draw(img)
def font(n):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try: return ImageFont.truetype(p, n)
        except OSError: pass
    return ImageFont.load_default()
d.ellipse((30, 30, 100, 100), fill="#8899aa")
d.text((120, 35), "Wellness Daily", font=font(26), fill="black")
d.text((120, 68), "@fakeuser123 · 14 Oct 2026, 9:41 PM", font=font(20), fill="#667788")
d.text((30, 140), "St John's wort is natural, so it's safe to take", font=font(34), fill="black")
d.text((30, 190), "with other medicines.", font=font(34), fill="black")
d.text((30, 280), "#naturalhealth #wellness", font=font(24), fill="#1d9bf0")
d.text((30, 400), "💬 212      🔁 1.4K      ♥ 8,903      📊 52K views", font=font(24), fill="#667788")
img.save(sys.argv[1])
