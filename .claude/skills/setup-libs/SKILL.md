---
name: setup-libs
description: Register the Hackerfab KiCad libraries in the user's global KiCad settings (HACKERFAB_KICADLIBS path variable plus symbol/footprint library tables). Use for first-time setup after cloning, after pulling a change that adds a new library, or when KiCad can't find HF_ symbols, footprints or 3D models.
---

# Set up the Hackerfab libraries in KiCad

`scripts/install.py` edits KiCad 10's global settings: it sets the `HACKERFAB_KICADLIBS` path variable to
this checkout and adds every library under `symbols/` and `footprints/` to the global library tables.
It only touches `HF_*` entries and the one path variable, keeps a one-time `.hf-backup` of each file it
changes, and is safe to re-run. Run from the repository root.

1. Check prerequisites: KiCad **10** installed (its settings folder exists, see troubleshooting below)
   and Python 3.9+ (`python3 --version`).
2. **Ask the user to close KiCad** - it rewrites its settings on exit and would undo the change.
3. Preview: `python3 scripts/install.py --dry-run`. Show the user the settings folder it found and the
   changes, then ask to proceed.
4. Apply: `python3 scripts/install.py`.
5. Tell them to start KiCad and confirm: *Preferences -> Manage Symbol Libraries* lists `HF_ICs`,
   `HF_Passives`, ... and *Manage Footprint Libraries* lists `HF_Footprints`. Placing a symbol and opening
   the 3D viewer on its footprint confirms the path variable works.

Troubleshooting:
- "Could not find KiCad 10's settings folder": start KiCad 10 once, or pass `--config-dir`.
- 3D model not found: *Preferences -> Configure Paths* should list `HACKERFAB_KICADLIBS` pointing at this
  checkout. If they moved the clone, re-run `install.py`.
- After `git pull`, re-run `install.py` only if a new `.kicad_sym` or `.pretty` appeared.
