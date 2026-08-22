"""Unauthenticated Google Maps snapshot of the start-finish-line.

Pulls Google's public satellite/hybrid map tiles (mt*.google.com/vt, no API
key / OAuth), stitches a grid, marks the configured S/F line and crops a
small view. Pure standalone script - not wired into the pipeline.
"""
import io
import math
import pathlib
import sys
import urllib.request
from PIL import Image, ImageDraw

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
from media_tools.config import load_config
from media_tools.relap import line_length_m, line_midpoint

TRACK, LAYOUT = "kgv", "default"        # which [tracks.*] layout to draw
SF_LINE = load_config().tracks.tracks[TRACK].layouts[LAYOUT].start_finish.line()
SF_LAT, SF_LON = line_midpoint(SF_LINE)  # centre of the view
ZOOM = 19           # 19: the ~8 m line is several px long on screen
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
# The line itself, end to end - the gate a lap boundary is cut on.
ends = []
for lat, lon in SF_LINE:
    ex, ey = world_px(lat, lon, ZOOM)
    ends.append((ex - x0 * TILE - left, ey - y0 * TILE - top))
d.line([ends[0], ends[1]], fill=(255, 40, 40), width=3)
for ex, ey in ends:  # end caps: where the gate stops (past them = no crossing)
    d.ellipse([ex - 4, ey - 4, ex + 4, ey + 4], fill=(255, 40, 40))
d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 255, 255))
label = (
    f"S/F line  {SF_LINE[0][0]:.6f},{SF_LINE[0][1]:.6f} -> "
    f"{SF_LINE[1][0]:.6f},{SF_LINE[1][1]:.6f}  ({line_length_m(SF_LINE):.1f} m)"
)
d.rectangle([6, 6, 6 + 6 * len(label), 24], fill=(0, 0, 0))
d.text((10, 10), label, fill=(255, 255, 255))

img.save(OUT)
print("saved", OUT, img.size)
print("google maps:", f"https://maps.google.com/?q={SF_LAT:.7f},{SF_LON:.7f}")
