"""Shared helpers for the Hackerfab KiCad library tooling.

Pure standard library (Python 3.9+). KiCad files are S-expressions; instead of
fully parsing them we split on balanced parentheses and edit individual blocks.
That keeps the tooling independent of which KiCad release wrote the file.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "hf-libs.json"


class HFError(Exception):
    """A problem the user can fix (bad input, wrong KiCad version, ...)."""


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def path_var(cfg):
    return "${%s}" % cfg["path_variable"]


def symbols_dir():
    return ROOT / "symbols"


def footprint_dir(cfg):
    return ROOT / "footprints" / (cfg["footprint_library"] + ".pretty")


def model_dir(cfg):
    return ROOT / "3dmodels" / (cfg["footprint_library"] + ".3dshapes")


def sources_dir():
    return ROOT / "sources"


# --------------------------------------------------------------------------
# S-expression helpers
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')
_STR = r'"((?:[^"\\]|\\.)*)"'
_PROP = re.compile(r"\(property " + _STR + " " + _STR)


def esc(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def unesc(value):
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


def children(text):
    """(start, end) spans of the direct child lists of the first list in text."""
    spans = []
    depth = 0
    start = 0
    for m in _TOKEN.finditer(text):
        t = m.group()
        if t == "(":
            depth += 1
            if depth == 2:
                start = m.start()
        elif t == ")":
            if depth == 2:
                spans.append((start, m.end()))
            depth -= 1
            if depth == 0:
                break
    return spans


def remove_span(text, start, end):
    """Delete text[start:end] plus the whitespace/newline that led up to it."""
    ws = start
    while ws > 0 and text[ws - 1] in " \t":
        ws -= 1
    if ws > 0 and text[ws - 1] == "\n":
        ws -= 1
    return text[:ws] + text[end:]


def props(text):
    """[(name, value, start, end)] for the top-level (property ...) children."""
    out = []
    for s, e in children(text):
        m = _PROP.match(text, s)
        if m:
            out.append((unesc(m.group(1)), unesc(m.group(2)), s, e))
    return out


def get_prop(text, name):
    for n, v, _, _ in props(text):
        if n == name:
            return v
    return None


def add_prop(text, name, value, indent="\t\t"):
    block = (
        '\n{i}(property "{n}" "{v}"\n{i}\t(at 0 0 0)\n{i}\t(show_name no)\n'
        "{i}\t(do_not_autoplace no)\n{i}\t(hide yes)\n{i}\t(effects\n{i}\t\t(font\n"
        "{i}\t\t\t(size 1.27 1.27)\n{i}\t\t)\n{i}\t)\n{i})"
    ).format(i=indent, n=esc(name), v=esc(value))
    existing = props(text)
    if existing:
        at = existing[-1][3]
    else:
        at = children(text)[0][1]
    return text[:at] + block + text[at:]


def set_prop(text, name, value, indent="\t\t"):
    for n, _, s, _ in props(text):
        if n == name:
            m = _PROP.match(text, s)
            return text[: m.start(2)] + esc(value) + text[m.end(2):]
    return add_prop(text, name, value, indent)


def remove_prop(text, name):
    for n, _, s, e in props(text):
        if n == name:
            return remove_span(text, s, e)
    return text


# --------------------------------------------------------------------------
# Symbol libraries
# --------------------------------------------------------------------------

_SYM_NAME = re.compile(r"\(symbol " + _STR)
_PIN_NUMBER = re.compile(r"\(number " + _STR)


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._+\-]", "_", name)


def pin_numbers(symbol_text):
    return {unesc(n) for n in _PIN_NUMBER.findall(symbol_text)}


def lib_template(cfg):
    return (
        '(kicad_symbol_lib\n\t(version %d)\n\t(generator "hf_ingest")\n'
        '\t(generator_version "%d.0")\n)\n' % (cfg["max_format_version"]["symbol"], cfg["kicad_major"])
    )


class SymbolLib:
    """A .kicad_sym file held as {symbol name: block text}."""

    def __init__(self, path, cfg=None):
        self.path = Path(path)
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8")
        else:
            text = lib_template(cfg or load_config())
        spans = [(s, e) for s, e in children(text) if _SYM_NAME.match(text, s)]
        if spans:
            self.header = text[: spans[0][0]].rstrip()
        else:
            self.header = text[: text.rstrip().rfind(")")].rstrip()
        self.symbols = {}
        for s, e in spans:
            self.symbols[_SYM_NAME.match(text, s).group(1)] = text[s:e]

    @property
    def version(self):
        m = re.search(r"\(version (\d+)\)", self.header)
        return int(m.group(1)) if m else None

    def render(self):
        body = "".join("\n\t" + self.symbols[n] for n in sorted(self.symbols, key=str.lower))
        return self.header + body + "\n)\n"

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(), encoding="utf-8")


def load_symbol_libs(cfg=None):
    return {p.stem: SymbolLib(p, cfg) for p in sorted(symbols_dir().glob("*.kicad_sym"))}


def split_symbols(text):
    """Top-level symbols of a library text: {name: block}."""
    return {
        _SYM_NAME.match(text, s).group(1): text[s:e]
        for s, e in children(text)
        if _SYM_NAME.match(text, s)
    }


# --------------------------------------------------------------------------
# Footprints
# --------------------------------------------------------------------------

_FP_NAME = re.compile(r'\((?:footprint|module)\s+(?:"((?:[^"\\]|\\.)*)"|([^\s()]+))')
_MODEL = re.compile(r'\(model\s+(?:"((?:[^"\\]|\\.)*)"|([^\s()]+))')
_UUID = re.compile(r'\s*\((?:uuid|tstamp)\s+"?[0-9a-fA-F-]+"?\)')
_PAD_NAME = re.compile(r"\(pad " + _STR)

MODEL_TEMPLATE = (
    '\n\t(model "{p}"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n'
    "\t\t(rotate\n\t\t\t(xyz 0 0 0)\n\t\t)\n\t)"
)


def fp_name(text):
    m = _FP_NAME.match(text.lstrip())
    return (m.group(1) or m.group(2)) if m else None


def fp_version(text):
    m = re.search(r"\(version (\d+)\)", text)
    return int(m.group(1)) if m else None


def is_legacy_fp(text):
    return text.lstrip().startswith("(module")


def rename_fp(text, new):
    m = _FP_NAME.match(text)
    return text[: m.start()] + '(footprint "%s"' % esc(new) + text[m.end():]


def pad_names(text):
    return {unesc(m.group(1)) for s, _ in children(text) for m in [_PAD_NAME.match(text, s)] if m}


def pad_signature(text):
    """Hash of the land pattern (pads only, UUIDs ignored)."""
    sigs = []
    for s, e in children(text):
        if text.startswith("(pad ", s):
            sigs.append(re.sub(r"\s+", " ", _UUID.sub("", text[s:e])))
    return hashlib.sha256("\n".join(sorted(sigs)).encode()).hexdigest()


def model_path(text):
    for s, _ in children(text):
        m = _MODEL.match(text, s)
        if m:
            return unesc(m.group(1)) if m.group(1) is not None else m.group(2)
    return None


def set_model(text, path):
    """Point the footprint's 3D model at `path` (adding the block if missing)."""
    for s, _ in children(text):
        m = _MODEL.match(text, s)
        if m:
            return text[:s] + '(model "%s"' % esc(path) + text[m.end():]
    end = text.rstrip().rfind(")")
    return text[:end].rstrip() + MODEL_TEMPLATE.format(p=esc(path)) + "\n)\n"


def drop_model(text):
    for s, e in children(text):
        if _MODEL.match(text, s):
            return remove_span(text, s, e)
    return text


# --------------------------------------------------------------------------
# kicad-cli
# --------------------------------------------------------------------------

def _cli_version(cli):
    try:
        p = subprocess.run([cli, "version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"(\d+)\.(\d+)", p.stdout.strip())
    return (int(m.group(1)), p.stdout.strip()) if m else None


def find_kicad_cli(cfg):
    """Locate a kicad-cli whose major version matches the pinned KiCad version."""
    candidates = [os.environ.get("KICAD_CLI"), shutil.which("kicad-cli")]
    candidates += [
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
    ]
    for pattern in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",):
        candidates += sorted(glob.glob(pattern), reverse=True)
    found = []
    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        v = _cli_version(c)
        if v is None:
            continue
        if v[0] == cfg["kicad_major"]:
            return c
        found.append("%s (%s)" % (c, v[1]))
    if found:
        raise HFError(
            "This library is pinned to KiCad %d, but the kicad-cli found is: %s.\n"
            "Install KiCad %d (library files written by other versions are not compatible)."
            % (cfg["kicad_major"], "; ".join(found), cfg["kicad_major"])
        )
    raise HFError(
        "kicad-cli not found. Install KiCad %d (https://www.kicad.org/download/) or set KICAD_CLI "
        "to the kicad-cli path." % cfg["kicad_major"]
    )


def run_cli(cli, *args):
    p = subprocess.run([cli] + [str(a) for a in args], capture_output=True, text=True)
    if p.returncode != 0:
        raise HFError("kicad-cli %s failed: %s" % (" ".join(str(a) for a in args[:2]), (p.stderr or p.stdout).strip()))
    return p.stdout


def log(*a):
    print(*a, file=sys.stderr)
