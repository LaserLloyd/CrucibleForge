# Archive of past bench revisions

| Version | Date | Commit | Cases | Files excluded | Same cases as |
|---|---|---|---|---|---|
| 3.0.0 | 2026-08-18 | `53f70c5` | 203 | 1 | - |
| 3.4.0 | 2026-09-25 | `3d707c7` | 247 | 1 | - |
| 3.5.0 | 2026-09-29 | `2843cbf` | 256 | 1 | - |
| 3.5.1 | 2026-10-01 | `88c1696` (v3.5.1) | 276 | - | - |
| 3.6.0 | 2026-10-18 | `9af4531` | 276 | - | 3.5.1 |
| 3.7.0 | 2026-10-19 | `3c55e84` | 276 | - | 3.5.1 |
| 4.0.0 | 2026-11-01 | `6ee5db4` (v4.0.0) | 384 | 3 | - |
| 4.0.1 | 2026-11-01 | `c9f620f` (v4.0.1) | 384 | 3 | 4.0.0 |

- This is the questions (`cases/`), the bench profile (`profiles/bench*.yaml`) and the rubric docs of every past revision, kept for reference.
- The current bench does not run anything here, except where a profile names an archived `cases_dir`.
- Each version has a `MANIFEST.json` (commit, date, sha256 of every file, excluded files with the reason). `Same cases as` means the cases tree is byte-identical to that version, so only the manifest is stored.
- Files that fail today's privacy scan (the fictional fixture home `/home/alex/` is allowed; any other home path fails) or the EL-17 stated-adult-ages rule (older suites predate it) are left out, never edited; see `excluded` in the manifest. 3.4.0 has no release commit: it is the last commit before the 3.5.0 bump (3.5.0 likewise); 3.7.0 is the commit that set the version. <!-- scrub-ok: names the fictional fixture home the archive gate allows -->
- To add a revision at each release: `python3 scripts/archive_revision.py --rev <tag> --version X.Y.Z`, then add its row here (see docs/DEVELOPING.md).
