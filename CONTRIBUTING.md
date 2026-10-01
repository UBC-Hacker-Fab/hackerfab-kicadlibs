# Contributing

## Workflow

1. `git pull` on `main`, then branch: `git switch -c yourname/add-<part-or-topic>`
   (for example `anush/add-atmega328p`). Each member works on their own branches.
2. Add parts with `/add-parts` or `python3 scripts/ingest.py`. Don't hand-edit library files.
3. `python3 scripts/validate.py` must pass (the script runs it for you).
4. Commit only `symbols/`, `footprints/`, `3dmodels/`, `sources/`. Open a PR to `main`.

## Before you open the PR

- You downloaded the **KiCad** package for the exact part number you need (check the package, e.g.
  SOIC-8 vs TSSOP-8).
- You compared the footprint's pads and pin 1 marker against the manufacturer datasheet's land pattern.
  The pin/pad check only proves the pin numbers line up, not that the geometry is right.
- The 3D model sits on the pads with the right orientation (open the footprint's 3D view).
- Fill in missing fields the report warned about (Datasheet, Description, Manufacturer) by re-ingesting
  with `--replace` after fixing the source, or in the PR description if the download lacks them.
- Generic parts (plain resistors, capacitors, headers) usually belong in KiCad's standard libraries,
  not here.

## Reviewing

Check the PR report and `sources/*.json`, spot-check the footprint against the datasheet, and look for
unexpected new footprint variants (`<name>_<MPN>`): they mean the pads differ from an existing footprint
of the same name.

## Merge conflicts in a `.kicad_sym`

Two PRs adding parts to the same category can conflict. Resolve it by taking `main`'s version of the
`.kicad_sym` file (`git checkout --theirs` during a merge, `--ours` during a rebase), then re-adding
your own parts from their zips with `python3 scripts/ingest.py --replace <your zips>`. Footprints,
3D models and `sources/` files never conflict because each part has its own files.

## KiCad version

Use KiCad 10 only. Files saved by other versions break the library for everyone.
