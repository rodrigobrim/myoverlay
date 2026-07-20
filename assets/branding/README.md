# MyOverlay branding

Master files are the SVGs; PNGs and `app.ico` are generated from them.
To regenerate after editing an SVG: `.venv/Scripts/python assets/branding/render_assets.py`

| File | Use |
|---|---|
| `logo.svg` / `png/icon-*.png`, `png/logo-512.png` | Emblem on dark/transparent — app welcome page, dark docs |
| `logo-light.svg` / `png/logo-light-512.png` | Emblem for light backgrounds |
| `favicon.svg` | Bold simplified mark used for the small icon sizes (≤48 px) and the .ico |
| `app.ico` | Windows binary / installer icon (16–256 px) |
| `logo-horizontal.svg` / `.png` | Emblem + wordmark lockup, dark backgrounds (welcome page header) |
| `logo-horizontal-light.svg` / `.png` | Lockup for light backgrounds (docs, README light theme) |
| `github-social-preview.svg` / `.png` | GitHub repo social preview (upload 1280x640 PNG in repo Settings → Social preview) |

README theme-aware logo snippet:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/branding/png/logo-horizontal.png">
  <img src="assets/branding/png/logo-horizontal-light.png" alt="MyOverlay" width="600">
</picture>
```
