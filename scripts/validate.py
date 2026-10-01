#!/usr/bin/env python3
"""Check the library for broken links, bad formats and mismatched pins/pads.

    python3 scripts/validate.py            # fast structural checks (no KiCad needed)
    python3 scripts/validate.py --kicad-cli   # also make kicad-cli load every library

Exit status is 1 if any error is found. Warnings never fail the run.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import hflib
from hflib import ROOT


def validate_repo(cfg=None, use_kicad_cli=False):
    cfg = cfg or hflib.load_config()
    errors, warnings = [], []
    var = hflib.path_var(cfg)
    fp_lib = cfg["footprint_library"]
    max_sym = cfg["max_format_version"]["symbol"]
    max_fp = cfg["max_format_version"]["footprint"]

    def rel(p):
        return str(Path(p).relative_to(ROOT))

    # ---- footprints ----------------------------------------------------
    fp_texts = {}
    referenced_models = set()
    for f in sorted(hflib.footprint_dir(cfg).glob("*.kicad_mod")):
        text = f.read_text(encoding="utf-8")
        fp_texts[f.stem] = text
        if hflib.is_legacy_fp(text):
            errors.append("%s: legacy (module ...) format; re-run kicad-cli fp upgrade" % rel(f))
        if hflib.fp_name(text) != f.stem:
            errors.append("%s: footprint name %r does not match the file name" % (rel(f), hflib.fp_name(text)))
        v = hflib.fp_version(text)
        if v and v > max_fp:
            errors.append("%s: format version %d is newer than KiCad %d (max %d)" % (rel(f), v, cfg["kicad_major"], max_fp))
        mp = hflib.model_path(text)
        if mp is None:
            warnings.append("%s: no 3D model" % rel(f))
        elif not mp.startswith(var + "/"):
            errors.append("%s: 3D model path %r must start with %s/" % (rel(f), mp, var))
        else:
            target = ROOT / mp[len(var) + 1:]
            referenced_models.add(target.resolve())
            if not target.exists():
                errors.append("%s: 3D model file missing: %s" % (rel(f), mp))
            elif target.suffix.lower() in (".step", ".stp"):
                head = target.read_bytes()[:64]
                if b"ISO-10303-21" not in head:
                    errors.append("%s: not a valid STEP file (no ISO-10303-21 header)" % rel(target))

    for m in sorted(hflib.model_dir(cfg).glob("*")):
        if m.name != ".gitkeep" and m.resolve() not in referenced_models:
            warnings.append("%s: 3D model not used by any footprint" % rel(m))

    # ---- symbols ------------------------------------------------------------
    libs = hflib.load_symbol_libs(cfg)
    seen = {}
    used_fps = set()
    sources = {}
    for sf in sorted(hflib.sources_dir().glob("*.json")):
        try:
            sources[sf.stem] = json.loads(sf.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors.append("%s: invalid JSON (%s)" % (rel(sf), exc))

    for libname, lib in libs.items():
        if lib.version and lib.version > max_sym:
            errors.append(
                "%s: format version %d is newer than KiCad %d (max %d)" % (rel(lib.path), lib.version, cfg["kicad_major"], max_sym)
            )
        for name, block in lib.symbols.items():
            where = "%s:%s" % (libname, name)
            if name in seen:
                errors.append("%s: duplicate symbol (also in %s)" % (where, seen[name]))
            seen[name] = libname
            for field in cfg["required_fields"]:
                if not hflib.get_prop(block, field):
                    errors.append("%s: required field %r is empty" % (where, field))
            for field in cfg["recommended_fields"]:
                if not hflib.get_prop(block, field):
                    warnings.append("%s: field %r is empty" % (where, field))

            fp_ref = hflib.get_prop(block, "Footprint") or ""
            if fp_ref:
                lib_part, _, fp = fp_ref.partition(":")
                if lib_part != fp_lib:
                    errors.append("%s: Footprint %r must start with %s:" % (where, fp_ref, fp_lib))
                elif fp not in fp_texts:
                    errors.append("%s: footprint %r not found in %s" % (where, fp_ref, fp_lib))
                else:
                    used_fps.add(fp)
                    pins = hflib.pin_numbers(block)
                    pads = {p for p in hflib.pad_names(fp_texts[fp]) if p}
                    missing = sorted(pins - pads)
                    if missing:
                        errors.append(
                            "%s: pins %s have no matching pad in footprint %s" % (where, ", ".join(missing), fp)
                        )
                    extra = sorted(pads - pins)
                    if extra:
                        warnings.append("%s: footprint pads %s are not on the symbol" % (where, ", ".join(extra)))
            src = sources.get(hflib.safe_name(name))
            if src is None:
                warnings.append("%s: no provenance file sources/%s.json" % (where, hflib.safe_name(name)))
            elif src.get("pin_count") and src["pin_count"] != len(hflib.pin_numbers(block)):
                warnings.append(
                    "%s: part_info says %s pins, symbol has %d" % (where, src["pin_count"], len(hflib.pin_numbers(block)))
                )

    for stem in sorted(set(fp_texts) - used_fps):
        warnings.append("footprints/%s.pretty/%s.kicad_mod: not used by any symbol" % (fp_lib, stem))
    known = {hflib.safe_name(n) for n in seen}
    for stem in sorted(set(sources) - known):
        warnings.append("sources/%s.json: no matching symbol" % stem)

    # ---- kicad-cli load test ---------------------------------------------------------
    if use_kicad_cli:
        cli = hflib.find_kicad_cli(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            for lib in libs.values():
                try:
                    hflib.run_cli(cli, "sym", "export", "svg", lib.path, "-o", tmp)
                except hflib.HFError as exc:
                    errors.append("%s: kicad-cli could not load it (%s)" % (rel(lib.path), exc))
            for d in sorted((ROOT / "footprints").glob("*.pretty")):
                if any(d.glob("*.kicad_mod")):
                    try:
                        hflib.run_cli(cli, "fp", "export", "svg", d, "-o", tmp)
                    except hflib.HFError as exc:
                        errors.append("%s: kicad-cli could not load it (%s)" % (rel(d), exc))
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kicad-cli", action="store_true", help="also load every library with kicad-cli")
    args = ap.parse_args(argv)
    try:
        errors, warnings = validate_repo(use_kicad_cli=args.kicad_cli)
    except hflib.HFError as exc:
        print("error:", exc, file=sys.stderr)
        return 1
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:  ", e)
    print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
