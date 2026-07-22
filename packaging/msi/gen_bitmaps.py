"""Generate the MSI wizard's banner and side bitmaps from the branding assets.

WiX's stock WixUI_Bmp_Banner / WixUI_Bmp_Dialog are generic placeholders. This
composites the real MyOverlay logo onto correctly-sized 24-bit BMPs:

  banner.bmp  493x58   - top strip of the interior pages (emblem on the right;
                         the wizard draws the page title/description over the
                         left, so that area is left blank).
  dialog.bmp  493x312  - background of the Welcome / Finish pages: a tinted
                         sidebar with the emblem on the left, blank white on
                         the right where those pages draw their text.

IMPORTANT: both canvases are light, so this must use the *-light branding
variants (per assets/branding/README.md, "-light" means "for light
backgrounds" - dark wordmark and dark gauge ticks). The plain logo.svg /
logo-horizontal.png variants are the dark-background ones: their near-white
artwork is invisible on white, which is exactly how this page used to render.

Layout constraint: the Welcome/Finish dialogs draw their text from X=135
dialog units (WizardUI.wxs), and the bitmap is 493 px across 370 DU, so
1 DU = 493/370 px and the text starts at ~180 px. All artwork therefore stays
inside the left SIDEBAR_W px, leaving a margin before the text begins.

Run by build_msi.ps1 before candle. Single source of truth = the PNGs under
assets/branding/png, so a logo update flows through on the next build.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
BRAND = HERE.parents[1] / "assets" / "branding" / "png"
WHITE = (255, 255, 255)
# Very light neutral for the Welcome/Finish sidebar: enough to read as a
# deliberate panel, faint enough that a DPI-scaling rounding shift near the
# text column is invisible.
SIDEBAR = (244, 245, 247)
# Sidebar width in px. The page text starts at ~180 px (135 DU), so this
# leaves a comfortable margin.
SIDEBAR_W = 168


def _trim(logo: Image.Image) -> Image.Image:
    """Crop away the transparent padding around the artwork.

    The square emblem PNGs are mostly empty margin (the gauge occupies a wide
    band in the middle), so fitting them un-trimmed makes the logo render far
    smaller than the box it was given."""
    bbox = logo.getbbox()
    return logo.crop(bbox) if bbox else logo


def _paste_fit(canvas: Image.Image, logo: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Paste logo (RGBA) scaled to fit box (x, y, w, h), keeping aspect, using
    its alpha as the mask so transparent areas show the canvas underneath."""
    x, y, w, h = box
    lw, lh = logo.size
    scale = min(w / lw, h / lh)
    nw, nh = max(1, int(lw * scale)), max(1, int(lh * scale))
    scaled = logo.resize((nw, nh), Image.LANCZOS)
    px, py = x + (w - nw) // 2, y + (h - nh) // 2
    canvas.paste(scaled, (px, py), scaled)


def main() -> None:
    # Light-background variants - see the note in the module docstring.
    emblem = _trim(Image.open(BRAND / "logo-light-512.png").convert("RGBA"))

    # --- banner: emblem on the right, blank left for the page title ---
    banner = Image.new("RGB", (493, 58), WHITE)
    _paste_fit(banner, emblem, (493 - 76, 6, 64, 46))
    banner.save(HERE / "banner.bmp")

    # --- dialog: tinted sidebar with the emblem centered in it; the right
    #     side stays white for the Welcome/Finish text ---
    dialog = Image.new("RGB", (493, 312), WHITE)
    dialog.paste(SIDEBAR, (0, 0, SIDEBAR_W, 312))
    _paste_fit(dialog, emblem, (16, 106, SIDEBAR_W - 32, 100))
    dialog.save(HERE / "dialog.bmp")

    print(f"wrote {HERE / 'banner.bmp'} and {HERE / 'dialog.bmp'}")


if __name__ == "__main__":
    main()
