"""Rasterize the branding SVGs into PNGs and the Windows .ico.

Run from the repo root:  .venv/Scripts/python assets/branding/render_assets.py
"""
from io import BytesIO
from pathlib import Path

import resvg_py
from PIL import Image

HERE = Path(__file__).parent
PNG_DIR = HERE / "png"
PNG_DIR.mkdir(exist_ok=True)


def render(svg_name: str, out_path: Path, width: int, height: int | None = None):
    svg = (HERE / svg_name).read_text(encoding="utf-8")
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height or width))
    out_path.write_bytes(png_bytes)
    print(f"{out_path.relative_to(HERE)}  {Image.open(BytesIO(png_bytes)).size}")
    return png_bytes


# Square emblem — app icon sizes (favicon.svg for <=48px, full emblem above)
icon_images = []
for size in (16, 32, 48, 64, 128, 256, 512):
    src = "favicon.svg" if size <= 48 else "logo.svg"
    data = render(src, PNG_DIR / f"icon-{size}.png", size)
    icon_images.append(Image.open(BytesIO(data)).convert("RGBA"))

# Windows .ico for the packaged binary (multi-resolution, from the bold plate mark)
ico_path = HERE / "app.ico"
ico_base = bytes(resvg_py.svg_to_bytes(svg_string=(HERE / "favicon.svg").read_text(encoding="utf-8"), width=256))
Image.open(BytesIO(ico_base)).convert("RGBA").save(
    ico_path, format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"app.ico  {[s for s in (16, 32, 48, 64, 128, 256)]}")

# Lockups and banners
render("logo.svg", PNG_DIR / "logo-512.png", 512)
render("logo-light.svg", PNG_DIR / "logo-light-512.png", 512)
render("logo-horizontal.svg", PNG_DIR / "logo-horizontal.png", 1200, 400)
render("logo-horizontal-light.svg", PNG_DIR / "logo-horizontal-light.png", 1200, 400)
render("github-social-preview.svg", PNG_DIR / "github-social-preview.png", 1280, 640)
