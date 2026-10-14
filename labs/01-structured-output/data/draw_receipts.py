"""Draw the synthetic receipts in data/receipts from the texts in data/source.

Real receipts arrive as phone photos, PDFs and email bodies, so the lab gets all three. Every merchant,
card number and guest name here is made up. The lab never runs this; rerun it only to change a receipt:

    uv run --no-project --with playwright==1.63.0 python data/draw_receipts.py
"""

import html
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
SOURCE, OUT = HERE / "source", HERE / "receipts"
# name -> (tilt in degrees, faded): a phone photo of a till slip on a desk.
PHOTOS = {
    "coffee": (-2.5, False),
    "bangalore-cafe": (1.8, False),
    "berlin-restaurant": (-1.2, False),
    "grocery-coupon": (2.6, False),
    "pharmacy": (-3.4, True),
}
PDFS = ["hotel", "hardware"]
EMAILS = ["taxi"]

NOISE = (
    "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'>"
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter>"
    "<rect width='300' height='300' filter='url(%23n)' opacity='.35'/></svg>\")"
)

PHOTO = """<!doctype html><html><head><style>
body {{ margin: 0 }}
.desk {{ padding: 70px 90px; display: inline-block; background: {noise}, linear-gradient(135deg, #6b5440, #4a3828 60%, #5a4532) }}
.slip {{ transform: perspective(1400px) rotateX(4deg) rotate({tilt}deg); padding: 34px 30px 40px;
  background: {noise}, linear-gradient(180deg, #fbf8f1, #f1ecdf 70%, #e9e3d4);
  box-shadow: 0 18px 30px rgba(0,0,0,.45), 0 2px 4px rgba(0,0,0,.3); clip-path: polygon({zigzag}); {fade} }}
pre {{ margin: 0; font: 15px/1.45 'Courier New', monospace; color: {ink} }}
</style></head><body><div class="desk"><div class="slip"><pre>{text}</pre></div></div></body></html>"""

DOCUMENT = """<!doctype html><html><body style="margin:0;font-family:Helvetica,Arial,sans-serif;color:#1d1d1f">
<div style="padding:56px 64px">
 <div style="border-bottom:3px solid #1d1d1f;padding-bottom:14px;margin-bottom:28px;display:flex;justify-content:space-between">
  <div style="font-size:22px;font-weight:700;letter-spacing:.04em">{merchant}</div>
  <div style="font-size:12px;color:#666;text-align:right">{kind}<br>Keep for your records</div>
 </div>
 <pre style="font:13px/1.6 Menlo,'Courier New',monospace;margin:0">{text}</pre>
 <div style="margin-top:48px;font-size:11px;color:#888">Questions about this {kind_lower}? Reply to the email it came with.</div>
</div></body></html>"""


def zigzag(teeth: int = 28, depth: float = 1.2) -> str:
    """A torn till-roll edge, top and bottom, as clip-path points in percent."""
    step = 100 / teeth
    top = [f"{i * step:.2f}% {depth if i % 2 else 0}%" for i in range(teeth + 1)]
    bottom = [f"{100 - i * step:.2f}% {100 - (depth if i % 2 else 0)}%" for i in range(teeth + 1)]
    return ",".join(top + bottom)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1.5)
        for name, (tilt, faded) in PHOTOS.items():
            text = html.escape((SOURCE / f"{name}.txt").read_text().rstrip())
            # A thermal slip left in a pocket: grey ink, low contrast, a soft blur.
            fade = "filter:contrast(.75) blur(.5px);" if faded else ""
            ink = "#5a5a5a" if faded else "#222"
            page.set_content(PHOTO.format(noise=NOISE, tilt=tilt, zigzag=zigzag(), fade=fade, ink=ink, text=text))
            page.locator(".desk").screenshot(path=OUT / f"{name}.jpg", type="jpeg", quality=80)
        for name in PDFS:
            lines = (SOURCE / f"{name}.txt").read_text().rstrip().splitlines()
            kind = "Folio" if name == "hotel" else "Invoice"
            page.set_content(DOCUMENT.format(
                merchant=html.escape(lines[0]), kind=kind, kind_lower=kind.lower(),
                text=html.escape("\n".join(lines[1:])),
            ))
            page.pdf(path=OUT / f"{name}.pdf", format="Letter")
        browser.close()
    for name in EMAILS:
        shutil.copy(SOURCE / f"{name}.txt", OUT / f"{name}.txt")
    print(f"drew {len(PHOTOS)} photos, {len(PDFS)} PDFs and {len(EMAILS)} email receipt(s) into {OUT}")


if __name__ == "__main__":
    main()
