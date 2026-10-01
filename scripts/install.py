#!/usr/bin/env python3
"""Register the Hackerfab libraries in KiCad's global settings.

    python3 scripts/install.py             # do it (close KiCad first)
    python3 scripts/install.py --dry-run   # show what would change

Sets the HACKERFAB_KICADLIBS path variable to this checkout and adds every library
in symbols/ and footprints/ to the global sym-lib-table / fp-lib-table. Safe to
re-run: existing HF_* entries are replaced, nothing else is touched. Re-run it after
pulling if a new library file was added.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import hflib
from hflib import HFError, ROOT


def config_dir(cfg, override=None):
    if override:
        return Path(override).expanduser()
    ver = "%d.0" % cfg["kicad_major"]
    home = Path.home()
    if sys.platform == "darwin":
        cands = [home / "Library/Preferences/kicad" / ver]
    elif sys.platform.startswith("win"):
        cands = [Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "kicad" / ver]
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        cands = [xdg / "kicad" / ver, home / ".var/app/org.kicad.KiCad/config/kicad" / ver]
    for c in cands:
        if c.is_dir():
            return c
    raise HFError(
        "Could not find KiCad %d's settings folder (looked in: %s).\n"
        "Start KiCad %d once so it creates it, or pass --config-dir." % (
            cfg["kicad_major"], ", ".join(str(c) for c in cands), cfg["kicad_major"])
    )


def kicad_running():
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
            return any(n in out for n in ("kicad.exe", "eeschema.exe", "pcbnew.exe"))
        for name in ("kicad", "eeschema", "pcbnew"):
            if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
                return True
    except OSError:
        pass
    return False


def backup(path):
    bak = path.with_name(path.name + ".hf-backup")
    if path.exists() and not bak.exists():
        shutil.copy(path, bak)


def set_path_variable(cfg, cdir, dry):
    path = cdir / "kicad_common.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    env = data.setdefault("environment", {})
    if not isinstance(env.get("vars"), dict):
        env["vars"] = {}
    value = str(ROOT).replace("\\", "/")
    changed = env["vars"].get(cfg["path_variable"]) != value
    env["vars"][cfg["path_variable"]] = value
    if changed and not dry:
        backup(path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        m = re.search(r"\n([ \t]+)\S", text)
        path.write_text(json.dumps(data, indent=m.group(1) if m else 2) + "\n", encoding="utf-8")
    return changed, value


_HF_ENTRY = re.compile(r'^[ \t]*\(lib \(name "HF_[^"]*"\).*\)[ \t]*\r?\n', re.M)


def update_table(path, kind, entries, dry):
    """Replace all HF_* entries in a lib table with `entries`; returns True if the file changed."""
    text = path.read_text(encoding="utf-8") if path.exists() else "(%s\n\t(version 7)\n)\n" % kind
    stripped = _HF_ENTRY.sub("", text)
    end = stripped.rstrip().rfind(")")
    lines = "".join(
        '\t(lib (name "%s") (type "KiCad") (uri "%s") (options "") (descr "%s"))\n' % (n, u, d) for n, u, d in entries
    )
    new = stripped[:end].rstrip("\n") + "\n" + lines + stripped[end:]
    if new == text:
        return False
    if not dry:
        backup(path)
        path.write_text(new, encoding="utf-8")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", help="KiCad settings folder (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = ap.parse_args(argv)
    cfg = hflib.load_config()
    var = hflib.path_var(cfg)
    try:
        cdir = config_dir(cfg, args.config_dir)
    except HFError as exc:
        print("error:", exc, file=sys.stderr)
        return 1

    sym_entries = [
        (p.stem, "%s/symbols/%s" % (var, p.name), cfg["symbol_libraries"].get(p.stem, "Hackerfab symbols"))
        for p in sorted((ROOT / "symbols").glob("*.kicad_sym"))
    ]
    fp_entries = [
        (p.stem, "%s/footprints/%s" % (var, p.name), "Hackerfab footprints")
        for p in sorted((ROOT / "footprints").glob("*.pretty"))
    ]

    if kicad_running() and not args.dry_run:
        print("warning: KiCad appears to be running. Close it first, or it may overwrite these changes.")
    print("KiCad settings: %s%s" % (cdir, "  (dry run)" if args.dry_run else ""))
    changed, value = set_path_variable(cfg, cdir, args.dry_run)
    print("  %-22s %s -> %s" % ("path variable", cfg["path_variable"], value) + ("" if changed else "  (already set)"))
    for fname, kind, entries in (("sym-lib-table", "sym_lib_table", sym_entries), ("fp-lib-table", "fp_lib_table", fp_entries)):
        ch = update_table(cdir / fname, kind, entries, args.dry_run)
        print("  %-22s %s%s" % (fname, ", ".join(e[0] for e in entries), "" if ch else "  (already up to date)"))
    if not args.dry_run:
        print("\nDone. Start KiCad and look for the HF_* libraries in the symbol/footprint choosers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
