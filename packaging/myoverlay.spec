# PyInstaller spec for the myoverlay launcher (onedir).
#
# The launcher imports media_tools from the PULLED repo at runtime, so
# PyInstaller cannot discover the pipeline's dependencies by static analysis
# - every third-party package the pipeline uses must be collected here
# explicitly. When pyproject.toml gains a new dependency, add it below and
# rebuild.

from PyInstaller.utils.hooks import collect_all

datas = [
    ("vendor/git", "git"),
    ("vendor/ffmpeg", "ffmpeg"),
]
binaries = []
hiddenimports = []

PIPELINE_PACKAGES = [
    # core
    "typer", "click", "rich", "shellingham",
    "pydantic", "pydantic_core", "pydantic_settings", "annotated_types",
    "dotenv",
    "watchdog", "psutil",
    # data
    "numpy", "pandas", "pyarrow", "dateutil", "tzdata", "six",
    "libxrk",
    # media / overlay
    "PIL", "gpxpy", "arabic_reshaper", "bidi",
    # youtube
    "googleapiclient", "google_auth_oauthlib", "google.auth", "google.oauth2",
    "google_auth_httplib2", "httplib2", "uritemplate", "requests_oauthlib",
    "oauthlib", "requests", "certifi", "charset_normalizer", "idna", "urllib3",
    "pyparsing", "rsa", "pyasn1", "pyasn1_modules", "cachetools",
    # rs3 automation
    "pywinauto", "comtypes", "win32ctypes",
]

for pkg in PIPELINE_PACKAGES:
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue  # optional package not present in the build env
    datas += d
    binaries += b
    hiddenimports += h

# The review GUI (media_tools.gui) is pulled from the repo at runtime, so
# PyInstaller cannot see its tkinter import statically. Force tkinter + tcl/tk
# into the bundle (the _tkinter hook pulls the tcl/tk data dirs) so
# `myoverlay gui` works in the frozen exe.
hiddenimports += ["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "_tkinter"]

a = Analysis(
    ["myoverlay_launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="myoverlay",
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="myoverlay",
)
