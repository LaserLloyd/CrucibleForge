# rotate.sh

Nightly backup rotation for the home server (runs from a systemd timer as
`rotate.sh --keep 7 /var/mnt/backup/archives`). Bash + coreutils only; it
must also run on a Debian box (so: no GNU-only `find -printf`, no `sort -V`,
no `stat` format flags).

    rotate.sh [--dry-run] [--keep N] DIR

* Backup files are named `<prefix>-YYYYMMDD-HHMMSS.tar.zst`. The prefix may
  contain spaces, dashes and dots (`home server-db-2026-10-25-...` would have
  the prefix `home server-db`, since the stamp is the last two dash fields).
* Files in DIR (not in subfolders) that do not match that pattern are never
  touched.
* For each prefix, keep the N newest backups (default 3) and delete the rest.
  "Newest" means the stamp in the file name, NOT the file's mtime (rsync and
  restores rewrite mtimes).
* For each deleted file print `deleted <file name>`; with `--dry-run` print
  `would delete <file name>` and delete nothing. Print the lines in
  byte-wise (LC_ALL=C) order of the file names; print nothing else on stdout.
* Exit 0 on success; exit 2 with a usage message on stderr for bad arguments
  (no DIR, unknown option, N not a whole number >= 1); exit 3 with a message
  on stderr when DIR is not a directory.
