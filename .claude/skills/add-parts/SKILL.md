---
name: add-parts
description: Add newly downloaded Component Search Engine (SamacSys) or SnapEDA KiCad parts to the Hackerfab KiCad libraries. Use when the user says they downloaded parts, wants to add a symbol/footprint/3D model to the shared library, or runs /add-parts. Scans their Downloads folder, converts and checks the parts, updates the libraries, and prepares a branch and PR.
---

# Add downloaded parts to the shared library

`scripts/ingest.py` does all the file work (convert, rewrite paths, merge, validate). Your job is to run
it, handle the decisions it surfaces, explain the result, and get the change onto a branch / PR.
**Never hand-edit `.kicad_sym`, `.kicad_mod` or `sources/*.json`** - if something is wrong, fix the input
or the script, then re-run.

Run everything from the repository root.

## 1. Pre-flight

- `git status -sb` and `git branch --show-current`. If the working tree already has uncommitted library
  changes, say so and ask before continuing.
- Library changes go on a personal branch, never directly on `main` (the maintainer sets up `main` by hand;
  if the user explicitly says to commit to `main`, that is fine). If on `main`, you will create the branch
  in step 5.
- `git fetch origin` and, if the branch is behind `origin/main`, tell the user and offer to update first
  so the symbol files don't conflict.

## 2. See what is there (dry run)

```
python3 scripts/ingest.py --dry-run --json
```

If the user named specific files or folders, pass them as arguments instead of scanning Downloads.
Add `--since 90` if they say the download is older than 30 days.

Read the JSON `results` (each has `part`, `status`, `library`, `footprint`, `footprint_reused`,
`warnings`, `errors`, `message`):

| status | meaning / what to do |
|---|---|
| `would_add` | fine |
| `exists` | already in the library; skip it. Only use `--replace` if the user says they want to overwrite it. |
| `needs_category` | the part's category is not in `hf-libs.json` (SnapEDA downloads never carry one). Ask the user which library from `choices` (use AskUserQuestion), then pass `--assign PART=LIBRARY`. If this category will come up again, offer to add it to `category_map` in `hf-libs.json`. |
| `skipped` | not a usable part package (for example an `.epw` loader file). Tell the user how to download the right one: on componentsearchengine.com or snapeda.com, download the part's **KiCad** zip. |
| `error` | do not add it. Explain in plain words. Pin/pad mismatch usually means the symbol and footprint in the download don't belong together - suggest re-downloading, and only use `--force` if the user insists. |

If `fatal` is present (for example "pinned to KiCad 10"), relay it: members must have **KiCad 10**
installed, because files written by other versions break the library for everyone.

If nothing was found, check `~/Downloads` for the zip with the user and try passing the path directly.

## 3. Apply

Re-run the same command without `--dry-run` (keep any `--assign` / `--replace` flags). Then report:

- each part: library, footprint (and whether an identical footprint was **reused** or a `_<MPN>` **variant**
  was created because the land pattern differed), and 3D model present or not;
- every warning in plain language, including `validation` warnings (missing Datasheet / Manufacturer fields etc.);
- anything in `validation.errors` is a failure: investigate and fix before committing.

For generic passives (plain resistors/capacitors with no special MPN), mention that KiCad's standard
libraries may be a better source than adding one-off symbols.

Optional extra check when time allows: `python3 scripts/validate.py --kicad-cli`.

## 4. Review the diff

`git status --short`. Expected changes are only under `symbols/`, `footprints/`, `3dmodels/` and `sources/`.
Do not stage anything else, and do not delete the user's downloaded zips.

## 5. Branch and commit

- If on `main`, create a branch named after the member: `<first-name>/add-<mpn-or-short-topic>`
  (take the name from `git config user.name`, lowercased; ask if it is unset).
- Stage only the library paths: `git add symbols footprints 3dmodels sources`.
- Commit message: `Add <MPN list>` with a short body listing library and footprint per part.

## 6. Push and PR (ask first)

Pushing is visible to others, so ask the user before pushing. If they agree:

```
git push -u origin <branch>
gh pr create --fill --body "<table of parts: MPN, library, footprint, new/reused; plus any warnings>"
```

If `gh` is not installed, give them the push result's compare URL instead. Mention that the PR checks run
`scripts/validate.py` and a reviewer will look at the footprint against the datasheet.
