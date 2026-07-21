"""Generate the MSI wizard's banner and side bitmaps from the branding assets.

WiX's stock WixUI_Bmp_Banner / WixUI_Bmp_Dialog are generic placeholders. This
composites the real MyOverlay logo onto correctly-sized 24-bit BMPs:

  banner.bmp  493x58   - top strip of the interior pages (logo on the right;
                         the wizard draws the page title/description over the
                         left, so that area is left blank).
  dialog.bmp  493x312  - left panel of the Welcome / Finish pages (logo +
                         wordmark in the left column; the pages draw their
                         text over the right, so that area is left blank).

Run by build_msi.ps1 before candle. Single source of truth = the PNGs under
assets/branding/png, so a logo update flows through on the next build.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
BRAND = HERE.parents[1] / "assets" / "branding" / "png"
WHITE = (255, 255, 255)


def _paste_fit(canvas: Image.Image, logo: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Paste logo (RGBA) scaled to fit box (x, y, w, h), keeping aspect, using
    its alpha as the mask so the transparent areas show the white canvas."""
    x, y, w, h = box
    lw, lh = logo.size
    scale = min(w / lw, h / lh)
    nw, nh = max(1, int(lw * scale)), max(1, int(lh * scale))
    scaled = logo.resize((nw, nh), Image.LANCZOS)
    px, py = x + (w - nw) // 2, y + (h - nh) // 2
    canvas.paste(scaled, (px, py), scaled)


def main() -> None:
    icon = Image.open(BRAND / "icon-256.png").convert("RGBA")
    wordmark = Image.open(BRAND / "logo-horizontal.png").convert("RGBA")

    # --- banner: logo icon on the right, blank left for the page title ---
    banner = Image.new("RGB", (493, 58), WHITE)
    _paste_fit(banner, icon, (493 - 58, 4, 54, 50))
    banner.save(HERE / "banner.bmp")

    # --- dialog: wordmark in the left column, blank right for the page text ---
    dialog = Image.new("RGB", (493, 312), WHITE)
    _paste_fit(dialog, wordmark, (12, 90, 150, 130))
    dialog.save(HERE / "dialog.bmp")

    print(f"wrote {HERE / 'banner.bmp'} and {HERE / 'dialog.bmp'}")


if __name__ == "__main__":
    main()
