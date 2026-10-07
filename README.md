# Hackerfab KiCad libraries

Shared KiCad **10** symbols, footprints and 3D models for all Hackerfab projects.
Parts come from [Component Search Engine](https://componentsearchengine.com/) or
[SnapEDA](https://www.snapeda.com/) KiCad downloads and are
added with a script (or the `/add-parts` Claude skill) so every part looks the same and is checked
before it lands.

## Use the libraries

Requirements: KiCad 10, Python 3.9+, git.

```sh
git clone <this repo> hackerfab-kicadlibs
cd hackerfab-kicadlibs
# close KiCad, then:
python3 scripts/install.py
```

Start KiCad: the `HF_*` symbol libraries and `HF_Footprints` are now in your global libraries.
`git pull` to get new parts. Re-run `install.py` only if a new library file was added.

If you use Claude Code, run `claude` in this folder and ask for `/setup-libs`.

## Add parts

1. Download the part's **KiCad** zip from Component Search Engine or SnapEDA (it lands in `~/Downloads`).
2. On a branch named after you (`yourname/add-<part>`), run either:
   - Claude Code in this folder: `/add-parts`
   - or `python3 scripts/ingest.py` (add `--dry-run` first to preview)
3. Review the report, commit, open a PR. See [CONTRIBUTING.md](CONTRIBUTING.md).

What the ingest does for each part:

| In the download | What we do |
|---|---|
| legacy `.lib` symbol | convert to `.kicad_sym` with `kicad-cli` |
| footprint link `QFP...` with no library | rewrite to `HF_Footprints:QFP...` |
| 3D model `(model Part.stp` (relative path) | copy the STEP in, point at `${HACKERFAB_KICADLIBS}/3dmodels/...` |
| generic footprint name reused by another part | reuse it if the pads are identical, otherwise add `<name>_<MPN>` |
| symbol pins vs footprint pads | must match, or the part is rejected |
| vendor price/stock/purchase link fields | dropped (they go stale) |
| SnapEDA field names (`MF`, `MP`) | mapped to `Manufacturer_Name` / `Manufacturer_Part_Number` |
| ~65 files for other EDA tools | ignored |

## Layout

```
symbols/                  HF_ICs, HF_Passives, HF_Connectors, HF_Discrete, HF_Misc (.kicad_sym)
footprints/HF_Footprints.pretty/      one .kicad_mod per footprint
3dmodels/HF_Footprints.3dshapes/      STEP models
sources/<part>.json       where each part came from (vendor, part ID, download date, archive hash)
scripts/                  ingest.py, validate.py, install.py, hflib.py
hf-libs.json              pinned KiCad version, category -> library map, required fields
.claude/skills/           /add-parts and /setup-libs
```

## Checks

```sh
python3 scripts/validate.py              # structure, links, pin/pad match, format versions
python3 scripts/validate.py --kicad-cli  # also make KiCad load every library
python3 -m unittest discover -s tests
```

CI runs the first and last on every PR.

## Why KiCad 10 only

KiCad can't open files written by a newer version. `kicad-cli` writes its own version's format,
so one contributor on a newer KiCad would make the whole library unreadable for everyone else.
`ingest.py` refuses to run with any other major version, and `validate.py` flags files newer than
the limits in `hf-libs.json`. When the team moves to a newer KiCad, update `hf-libs.json`
(`kicad_major`, `max_format_version`) in one PR.

## Not covered yet

- Ultra Librarian zips: the layout differs; add a loader in `scripts/ingest.py` when someone needs one.
- SnapEDA zips carry no category, datasheet or 3D model in many cases: you will be asked which library
  to use, and the validator will warn about the missing fields.
- 3D models are ~1 MB each, so the repo will grow. Consider Git LFS later if it gets heavy.
- No license file yet.
