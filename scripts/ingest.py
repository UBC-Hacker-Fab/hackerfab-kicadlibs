#!/usr/bin/env python3
"""Add Component Search Engine (SamacSys) and SnapEDA part downloads to the shared libraries.

    python3 scripts/ingest.py                  # scan ~/Downloads for new part zips
    python3 scripts/ingest.py PART.zip DIR     # ingest specific zips / extracted folders
    python3 scripts/ingest.py --dry-run        # show what would happen, change nothing

For each part it converts the legacy symbol with kicad-cli, rewrites the symbol's
footprint link and the footprint's 3D model path to the shared library, reuses an
identical existing footprint (or adds a _<MPN> variant when pads differ), merges
the symbol into the right category library, and records provenance in sources/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import hflib
import validate
from hflib import HFError, ROOT


class Part:
    def __init__(self, name, meta, sym_text, fp_name, fp_text, step, origin, sha=None):
        self.name = name
        self.meta = meta
        self.sym_text = sym_text
        self.fp_name = fp_name
        self.fp_text = fp_text
        self.step = step
        self.origin = origin
        self.sha = sha


# --------------------------------------------------------------------------
# Reading downloads
# --------------------------------------------------------------------------

def parse_info(path):
    info = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip()
    return info


def zip_part_prefixes(zf):
    names = zf.namelist()
    out = []
    for n in names:
        if n.endswith("part_info.txt"):
            prefix = n[: -len("part_info.txt")]
            if any(m.startswith(prefix + "KiCad/") for m in names):
                out.append(prefix)
    return out


def is_flat_kicad_zip(zf):
    """SnapEDA-style zip: one .kicad_sym and at least one .kicad_mod, and not a KiCad project."""
    names = [n.lower() for n in zf.namelist()]
    return (sum(n.endswith(".kicad_sym") for n in names) == 1
            and any(n.endswith(".kicad_mod") for n in names)
            and not any(n.endswith((".kicad_pcb", ".kicad_sch", ".kicad_pro")) for n in names))


def is_part_zip(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return bool(zip_part_prefixes(zf)) or is_flat_kicad_zip(zf)
    except (zipfile.BadZipFile, OSError):
        return False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def convert_symbols(cli, sym_src, tmp):
    inp = tmp / ("sym_in" + sym_src.suffix)
    shutil.copy(sym_src, inp)
    out = tmp / "sym_out.kicad_sym"
    hflib.run_cli(cli, "sym", "upgrade", "--force", inp, "-o", out)
    return hflib.split_symbols(out.read_text(encoding="utf-8"))


def convert_footprints(cli, mods, tmp):
    d = tmp / "fp.pretty"
    d.mkdir()
    for m in mods:
        shutil.copy(m, d / m.name)
    hflib.run_cli(cli, "fp", "upgrade", "--force", d)
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.kicad_mod"))}


def build_parts(cli, sym_src, mods, steps, meta, origin, sha, tmp):
    """Convert one symbol library + its footprints into Part objects."""
    symbols = convert_symbols(cli, sym_src, tmp)
    fps = convert_footprints(cli, mods, tmp)
    if not fps:
        raise HFError("no .kicad_mod footprint found in %s" % origin)
    parts = []
    for name, block in symbols.items():
        ref = (hflib.get_prop(block, "Footprint") or "").split(":")[-1]
        if ref in fps:
            fp = ref
        elif len(fps) == 1:
            fp = next(iter(fps))
        else:
            raise HFError("%s: cannot tell which footprint belongs to symbol %s" % (origin, name))
        step = None
        for s in steps:
            if s.stem.lower() == fp.lower():
                step = s
        if step is None and len(steps) == 1:
            step = steps[0]
        if step is None and steps:
            step = next((s for s in steps if s.stem.lower() == name.lower()), None)
        parts.append(Part(name, dict(meta), block, fp, fps[fp], step, origin, sha))
    return parts


def load_cse_root(cli, root, tmp, origin, sha):
    meta = parse_info(root / "part_info.txt")
    meta["Source"] = "componentsearchengine"
    kdir = next((d for d in root.iterdir() if d.is_dir() and d.name.lower() == "kicad"), None)
    if kdir is None:
        raise HFError("%s: no KiCad folder in the download" % origin)
    sym_src = next(iter(sorted(kdir.glob("*.kicad_sym"))), None) or next(iter(sorted(kdir.glob("*.lib"))), None)
    if sym_src is None:
        raise HFError("%s: no symbol (.lib/.kicad_sym) in the KiCad folder" % origin)
    if sym_src.suffix == ".lib":
        m = re.search(r"^#/(\d+)/", sym_src.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            meta["PartID"] = m.group(1)
    mods = sorted(kdir.glob("*.kicad_mod"))
    steps = sorted(p for d in root.iterdir() if d.is_dir() and d.name.lower() == "3d"
                   for p in d.iterdir() if p.suffix.lower() in (".stp", ".step"))
    return build_parts(cli, sym_src, mods, steps, meta, origin, sha, tmp)


_SNAPEDA_RENAME = {"MF": "Manufacturer_Name", "MANUFACTURER": "Manufacturer_Name", "MP": "Manufacturer_Part_Number"}


def adapt_snapeda(text):
    """Map SnapEDA's field names onto ours; returns (symbol text, extra metadata)."""
    meta = {}
    for old, new in _SNAPEDA_RENAME.items():
        v = hflib.get_prop(text, old)
        if v:
            if not hflib.get_prop(text, new):
                text = hflib.set_prop(text, new, v)
            meta.setdefault(new, v)
        text = hflib.remove_prop(text, old)
    link = hflib.get_prop(text, "SnapEDA_Link")
    if link:
        meta["SourceURL"] = link
    m = re.search(r"unipart_id=(\d+)", hflib.get_prop(text, "Purchase-URL") or "")
    if m:
        meta["PartID"] = m.group(1)
    for name, value, _, _ in hflib.props(text):
        if value == "None":
            text = hflib.remove_prop(text, name)
    return text, meta


def load_flat_dir(cli, root, tmp, origin, sha):
    syms = sorted(root.rglob("*.kicad_sym"))
    if len(syms) != 1:
        raise HFError("%s: expected one .kicad_sym, found %d" % (origin, len(syms)))
    mods = sorted(root.rglob("*.kicad_mod"))
    steps = sorted(p for p in root.rglob("*") if p.suffix.lower() in (".stp", ".step"))
    vendor = "snapeda" if "snapeda" in syms[0].read_text(encoding="utf-8", errors="replace").lower() else "unknown"
    parts = build_parts(cli, syms[0], mods, steps, {"Source": vendor}, origin, sha, tmp)
    for p in parts:
        if vendor == "snapeda":
            p.sym_text, extra = adapt_snapeda(p.sym_text)
            p.meta.update(extra)
        p.meta["PartNumber"] = hflib.get_prop(p.sym_text, "Manufacturer_Part_Number") or p.name
        p.meta["Manufacturer"] = hflib.get_prop(p.sym_text, "Manufacturer_Name")
    return parts


def load_source(cli, src, work):
    """Yield Part lists for one zip or folder. Raises HFError if it is not a part package."""
    parts = []
    if src.is_dir():
        roots = [p.parent for p in src.rglob("part_info.txt") if any(
            d.is_dir() and d.name.lower() == "kicad" for d in p.parent.iterdir())]
        if not roots:
            if any(src.rglob("*.kicad_sym")) and any(src.rglob("*.kicad_mod")):
                return load_flat_dir(cli, src, work, src.name, None)
            raise HFError("%s: not a part folder (no part_info.txt + KiCad/, and no .kicad_sym + .kicad_mod)" % src.name)
        for i, root in enumerate(roots):
            tmp = work / ("d%d" % i)
            tmp.mkdir()
            parts += load_cse_root(cli, root, tmp, src.name, None)
        return parts
    if not zipfile.is_zipfile(src):
        raise HFError("%s: not a zip file" % src.name)
    sha = sha256(src)
    with zipfile.ZipFile(src) as zf:
        prefixes = zip_part_prefixes(zf)
        if not prefixes and is_flat_kicad_zip(zf):
            tmp = work / "flat"
            tmp.mkdir()
            for n in zf.namelist():
                if n.lower().endswith((".kicad_sym", ".kicad_mod", ".stp", ".step")):
                    zf.extract(n, tmp)
            return load_flat_dir(cli, tmp, tmp, src.name, sha)
        if not prefixes:
            raise HFError("%s: not a usable part package (need a Component Search Engine zip with KiCad/, "
                          "or a SnapEDA KiCad zip; .epw loader files can't be used)" % src.name)
        for i, prefix in enumerate(prefixes):
            tmp = work / ("z%d" % i)
            tmp.mkdir()
            for n in zf.namelist():
                low = n.lower()
                if n.startswith(prefix) and not n.endswith("/") and (
                    n == prefix + "part_info.txt"
                    or n.startswith(prefix + "KiCad/")
                    or (n.startswith(prefix + "3D/") and low.endswith((".stp", ".step")))
                ):
                    zf.extract(n, tmp)
            parts += load_cse_root(cli, tmp / prefix if prefix else tmp, tmp, src.name, sha)
    return parts


def discover(paths, cfg, since_days):
    if paths:
        out = []
        for p in paths:
            p = Path(p).expanduser()
            if not p.exists():
                raise HFError("no such file or folder: %s" % p)
            out.append(p)
        return out
    cutoff = time.time() - since_days * 86400
    out = []
    for d in cfg["download_dirs"]:
        d = Path(d).expanduser()
        if d.is_dir():
            out += [z for z in sorted(d.glob("*.zip")) if z.stat().st_mtime >= cutoff and is_part_zip(z)]
    return out


# --------------------------------------------------------------------------
# Placing a part in the library
# --------------------------------------------------------------------------

def normalize_symbol(text, final_fp, cfg):
    text = hflib.set_prop(text, "Footprint", "%s:%s" % (cfg["footprint_library"], final_fp))
    desc, extra = hflib.get_prop(text, "Description"), hflib.get_prop(text, "Description_1")
    if desc and re.search(r"\s{2,}|^\s|\s$", desc):
        text = hflib.set_prop(text, "Description", re.sub(r"\s+", " ", desc).strip())
    if extra is not None:
        if not desc:
            text = hflib.set_prop(text, "Description", extra)
        text = hflib.remove_prop(text, "Description_1")
    for name in [n for n, _, _, _ in hflib.props(text)]:
        if any(re.search(p, name) for p in cfg["drop_field_patterns"]):
            text = hflib.remove_prop(text, name)
    return text


def ask_category(part, cfg):
    libs = list(cfg["symbol_libraries"])
    print("\n%s has category %r, which is not in hf-libs.json. Which library?" % (part.name, part.meta.get("PartCategory")))
    for i, name in enumerate(libs, 1):
        print("  %d) %s - %s" % (i, name, cfg["symbol_libraries"][name]))
    while True:
        choice = input("choice [1-%d]: " % len(libs)).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(libs):
            return libs[int(choice) - 1]


class Context:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.libs = hflib.load_symbol_libs(cfg)
        for name in cfg["symbol_libraries"]:
            self.libs.setdefault(name, hflib.SymbolLib(hflib.symbols_dir() / (name + ".kicad_sym"), cfg))
        self.index = {n: lib for lib, obj in self.libs.items() for n in obj.symbols}
        self.fp_dir = hflib.footprint_dir(cfg)
        self.model_dir = hflib.model_dir(cfg)

    def fp_refs(self, fp, excluding):
        ref = "%s:%s" % (self.cfg["footprint_library"], fp)
        return [n for lib in self.libs.values() for n, b in lib.symbols.items()
                if n != excluding and hflib.get_prop(b, "Footprint") == ref]


def choose_library(part, ctx):
    args, cfg = ctx.args, ctx.cfg
    lib = args.assign.get(part.name) or args.category
    if not lib:
        lib = cfg["category_map"].get(part.meta.get("PartCategory", "").lower())
    if lib is None and sys.stdin.isatty() and not args.json and not args.dry_run:
        lib = ask_category(part, cfg)
    if lib is not None and lib not in cfg["symbol_libraries"]:
        raise HFError("unknown library %r (choose from: %s)" % (lib, ", ".join(cfg["symbol_libraries"])))
    return lib


def place(part, ctx):
    cfg, args = ctx.cfg, ctx.args
    res = {"part": part.name, "source": part.origin, "status": None, "library": None, "footprint": None,
           "footprint_reused": False, "model": None, "warnings": [], "errors": []}
    old_lib = ctx.index.get(part.name)
    if old_lib and not args.replace:
        res.update(status="exists", library=old_lib, message="already in %s (use --replace to overwrite)" % old_lib)
        return res
    lib = choose_library(part, ctx)
    if lib is None:
        res.update(status="needs_category",
                   message="PartCategory %r is not mapped; re-run with --assign %s=<library>" % (
                       part.meta.get("PartCategory"), part.name),
                   choices=list(cfg["symbol_libraries"]))
        return res
    res["library"] = lib

    # ---- footprint: reuse, add, or add as a variant ---------------------------
    name0 = part.fp_name
    sig = hflib.pad_signature(part.fp_text)
    old_src = {}
    sf = hflib.sources_dir() / (hflib.safe_name(part.name) + ".json")
    if old_lib and sf.exists():
        old_src = json.loads(sf.read_text(encoding="utf-8"))

    def existing(name):
        p = ctx.fp_dir / (name + ".kicad_mod")
        return p.read_text(encoding="utf-8") if p.exists() else None

    final, reuse = name0, False
    cur = existing(name0)
    if cur is not None:
        owned = old_src.get("footprint") == name0 and not ctx.fp_refs(name0, part.name)
        if hflib.pad_signature(cur) == sig:
            reuse = True
        elif not owned:
            final = hflib.safe_name(name0 + "_" + part.name)
            cur2 = existing(final)
            if cur2 is not None and hflib.pad_signature(cur2) != sig:
                res.update(status="error", errors=["footprint %s already exists with different pads" % final])
                return res
            reuse = cur2 is not None
            res["warnings"].append("footprint %s exists with a different land pattern; %s %s" % (
                name0, "using existing variant" if reuse else "adding variant", final))
    fp_text = existing(final) if reuse else part.fp_text
    res["footprint"], res["footprint_reused"] = final, reuse
    if reuse and final == name0:
        res["warnings"].append("reusing existing footprint %s (same pads)" % final)

    # ---- checks -----------------------------------------------------------------
    pins = hflib.pin_numbers(part.sym_text)
    pads = {p for p in hflib.pad_names(fp_text) if p}
    missing = sorted(pins - pads)
    if missing and not args.force:
        res.update(status="error", errors=["symbol pins %s have no matching footprint pad - wrong download? "
                                           "(--force to add anyway)" % ", ".join(missing)])
        return res
    if missing:
        res["warnings"].append("pins %s have no matching pad (forced)" % ", ".join(missing))
    want = part.meta.get("PinCount")
    if want and want.isdigit() and int(want) != len(pins):
        res["warnings"].append("part_info says %s pins, symbol has %d" % (want, len(pins)))

    # ---- build the new files ----------------------------------------------------------
    model_name = None
    if not reuse:
        if hflib.get_prop(fp_text, "Value") == name0:
            fp_text = hflib.set_prop(fp_text, "Value", final, indent="\t")
        if hflib.fp_name(fp_text) != final:
            fp_text = hflib.rename_fp(fp_text, final)
        if part.step is not None:
            model_name = final + ".step"
            fp_text = hflib.set_model(fp_text, "%s/3dmodels/%s.3dshapes/%s" % (
                hflib.path_var(cfg), cfg["footprint_library"], model_name))
        else:
            fp_text = hflib.drop_model(fp_text)
            res["warnings"].append("no 3D model in the download")
    else:
        model_name = Path(hflib.model_path(fp_text) or "").name or None
    res["model"] = model_name
    sym_text = normalize_symbol(part.sym_text, final, cfg)

    if args.dry_run:
        res["status"] = "would_replace" if old_lib else "would_add"
        return res

    if not reuse:
        ctx.fp_dir.mkdir(parents=True, exist_ok=True)
        (ctx.fp_dir / (final + ".kicad_mod")).write_text(fp_text, encoding="utf-8")
        if part.step is not None:
            ctx.model_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(part.step, ctx.model_dir / model_name)
    if old_lib and old_lib != lib:
        del ctx.libs[old_lib].symbols[part.name]
        ctx.libs[old_lib].save()
    ctx.libs[lib].symbols[part.name] = sym_text
    ctx.libs[lib].save()
    ctx.index[part.name] = lib

    pc = part.meta.get("PinCount")
    src = {
        "name": part.name,
        "manufacturer": part.meta.get("Manufacturer"),
        "mpn": part.meta.get("PartNumber"),
        "source": part.meta.get("Source", "componentsearchengine"),
        "source_url": part.meta.get("SourceURL"),
        "part_id": part.meta.get("PartID"),
        "part_category": part.meta.get("PartCategory"),
        "package_category": part.meta.get("PackageCategory"),
        "pin_count": int(pc) if pc and pc.isdigit() else None,
        "downloaded": part.meta.get("Downloaded"),
        "symbol_library": lib,
        "footprint": final,
        "footprint_original_name": name0,
        "footprint_reused": reuse,
        "model": model_name,
        "archive": part.origin,
        "archive_sha256": part.sha,
    }
    src = {k: v for k, v in src.items() if k != "source_url" or v}
    hflib.sources_dir().mkdir(exist_ok=True)
    sf.write_text(json.dumps(src, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    res["status"] = "replaced" if old_lib else "added"
    return res


# --------------------------------------------------------------------------

def print_report(report):
    for r in report["results"]:
        line = "%-28s %-14s" % (r["part"], r["status"])
        if r.get("library"):
            line += " %-14s" % r["library"]
        if r.get("footprint"):
            line += " fp=%s%s" % (r["footprint"], " (reused)" if r["footprint_reused"] else "")
        print(line)
        for w in r.get("warnings", []):
            print("    warning:", w)
        for e in r.get("errors", []):
            print("    ERROR:  ", e)
        if r.get("message"):
            print("   ", r["message"])
    v = report.get("validation")
    if v:
        for e in v["errors"]:
            print("VALIDATION ERROR:", e)
        print("validation: %d error(s), %d warning(s)" % (len(v["errors"]), len(v["warnings"])))


def main(argv=None):
    cfg = hflib.load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", help="part zips or extracted folders (default: scan download folders)")
    ap.add_argument("--since", type=int, default=cfg["download_max_age_days"], metavar="DAYS",
                    help="when scanning, only zips modified in the last DAYS days (default %(default)s)")
    ap.add_argument("--category", metavar="LIB", help="put every part in this symbol library")
    ap.add_argument("--assign", action="append", default=[], metavar="PART=LIB", help="library for one part")
    ap.add_argument("--replace", action="store_true", help="overwrite parts already in the library")
    ap.add_argument("--force", action="store_true", help="add even if symbol pins don't match the footprint pads")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    args = ap.parse_args(argv)
    args.assign = dict(a.split("=", 1) for a in args.assign if "=" in a)

    try:
        cli = hflib.find_kicad_cli(cfg)
        items = discover(args.sources, cfg, args.since)
    except HFError as exc:
        if args.json:
            print(json.dumps({"fatal": str(exc)}))
        else:
            print("error:", exc, file=sys.stderr)
        return 1

    ctx = Context(cfg, args)
    report = {"results": []}
    with tempfile.TemporaryDirectory(prefix="hf-ingest-") as work:
        for i, src in enumerate(items):
            wd = Path(work) / ("s%d" % i)
            wd.mkdir()
            try:
                parts = load_source(cli, src, wd)
            except HFError as exc:
                report["results"].append({"part": src.name, "source": src.name, "status": "skipped",
                                          "warnings": [], "errors": [], "footprint_reused": False, "message": str(exc)})
                continue
            for part in parts:
                try:
                    report["results"].append(place(part, ctx))
                except HFError as exc:
                    report["results"].append({"part": part.name, "source": src.name, "status": "error",
                                              "warnings": [], "errors": [str(exc)], "footprint_reused": False})
    if not items:
        report["message"] = "no new Component Search Engine part zips found (looked in: %s, last %d days)" % (
            ", ".join(cfg["download_dirs"]), args.since)
    if not args.dry_run and any(r["status"] in ("added", "replaced") for r in report["results"]):
        errors, warnings = validate.validate_repo(cfg)
        report["validation"] = {"errors": errors, "warnings": warnings}

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if report.get("message"):
            print(report["message"])
        print_report(report)
    bad = any(r["status"] == "error" for r in report["results"]) or bool(report.get("validation", {}).get("errors"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
