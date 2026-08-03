"""List and download sessions from an AiM MyChron6.

Picks the transport automatically - USB first, then WiFi - and joins the
logger's access point itself when it falls back to WiFi.

    python mychron.py list
    python mychron.py list --format csv --out sessions.csv
    python mychron.py get a_0186.xrz a_0185.xrz --out dl
    python mychron.py get --all --out dl
    python mychron.py --transport wifi list

Implementation lives in the aim/ package; this is only the CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aim import NoDeviceError, connect
from aim.catalog import as_catalog, render_table, rows_as_dicts


def cmd_list(args, dev) -> int:
    csv_text = dev.session_csv()
    columns, rows, _ = as_catalog(csv_text)

    if args.format == "csv":
        out = csv_text                      # verbatim, as the device sent it
    elif args.format == "json":
        out = json.dumps(rows_as_dicts(columns, rows), indent=2)
    else:
        out = render_table(columns, rows)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
        print(f"{len(rows)} sessions, {len(columns)} columns -> {args.out}")
    else:
        print(out)
        print(f"\n{len(rows)} sessions over {dev.kind}", file=sys.stderr)
    return 0


def cmd_get(args, dev) -> int:
    _columns, _rows, catalog = as_catalog(dev.session_csv())
    names = list(catalog) if args.all else list(args.names)
    if not names:
        print("give session names, or --all", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    failures = 0
    for name in names:
        meta = catalog.get(name)
        if meta is None:
            print(f"  {name}: not on the device", file=sys.stderr)
            failures += 1
            continue
        expected = int(meta["size"])
        dest = os.path.join(args.out, name)

        def progress(done, total, _n=name):
            sys.stderr.write(f"\r  {_n}  {done:>10,} / {total:,}")
            sys.stderr.flush()

        with open(dest, "wb") as fh:
            written = dev.fetch(name, fh, expected,
                                progress=None if args.quiet else progress)
        if not args.quiet:
            sys.stderr.write("\n")

        if written != expected:
            print(f"  {name}: got {written:,}, expected {expected:,}",
                  file=sys.stderr)
            failures += 1
        else:
            print(f"  {name}: ok {written:,} bytes -> {dest}", file=sys.stderr)
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--transport", choices=("usb", "wifi"),
                    help="force one transport instead of trying USB then WiFi")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list sessions on the logger")
    p_list.add_argument("--format", choices=("table", "csv", "json"),
                        default="table")
    p_list.add_argument("--out")

    p_get = sub.add_parser("get", help="download sessions")
    p_get.add_argument("names", nargs="*")
    p_get.add_argument("--all", action="store_true")
    p_get.add_argument("--out", default=".")
    p_get.add_argument("--quiet", action="store_true")

    args = ap.parse_args(argv)

    try:
        dev = connect(prefer=args.transport)
    except NoDeviceError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    try:
        return cmd_list(args, dev) if args.cmd == "list" else cmd_get(args, dev)
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
