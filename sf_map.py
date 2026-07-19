"""Unauthenticated Google Maps snapshot of the start-finish-coordinate.

Pulls Google's public satellite/hybrid map tiles (mt*.google.com/vt, no API
key / OAuth), stitches a grid, marks the exact S/F point and crops a small
view. Pure standalone script - not wired into the pipeline.
"""
import io
import math
import urllib.request
from PIL import Image, ImageDraw

SF_LAT, SF_LON = -23.60492, -46.83631   # start-finish-coordinate (default) - configured S/F
ZOOM = 18
LYRS = "y"          # y = hybrid (satellite + labels); s = pure satellite
GRID = 3            # NxN tiles stitched
CROP = 460          # final crop size (px), centred on the point
OUT = r"C:\Users\rodrigobrim\repos\media-tools\sf_map.png"

TILE = 256
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def world_px(lat, lon, z):
    n = 2 ** z * TILE
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def fetch_tile(x, y, z, i):
    url = f"https://mt{i % 4}.google.com/vt/lyrs={LYRS}&x={x}&y={y}&z={z}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://maps.google.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


px, py = world_px(SF_LAT, SF_LON, ZOOM)
ctx, cty = int(px // TILE), int(py // TILE)          # centre tile
half = GRID // 2
x0, y0 = ctx - half, cty - half

canvas = Image.new("RGB", (GRID * TILE, GRID * TILE))
for gx in range(GRID):
    for gy in range(GRID):
        tile = fetch_tile(x0 + gx, y0 + gy, ZOOM, gx + gy)
        canvas.paste(tile, (gx * TILE, gy * TILE))

# point position within the stitched canvas
mx = px - x0 * TILE
my = py - y0 * TILE

# crop centred on the point
left = int(mx - CROP / 2)
top = int(my - CROP / 2)
img = canvas.crop((left, top, left + CROP, top + CROP))
cx, cy = mx - left, my - top

d = ImageDraw.Draw(img)
# crosshair + ring on the exact coordinate
r = 11
d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 40, 40), width=3)
d.line([cx - r - 8, cy, cx + r + 8, cy], fill=(255, 40, 40), width=2)
d.line([cx, cy - r - 8, cx, cy + r + 8], fill=(255, 40, 40), width=2)
d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 40, 40))
label = f"S/F  {SF_LAT:.7f}, {SF_LON:.7f}"
d.rectangle([6, 6, 6 + 8 * len(label), 24], fill=(0, 0, 0))
d.text((10, 10), label, fill=(255, 255, 255))

img.save(OUT)
print("saved", OUT, img.size)
print("google maps:", f"https://maps.google.com/?q={SF_LAT:.7f},{SF_LON:.7f}")
