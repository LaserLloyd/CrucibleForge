#!/bin/bash
# rotate.sh [--dry-run] [--keep N] DIR : keep the newest N backups per prefix
keep=3
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --keep) keep=$2; shift 2 ;;
    *) dir=$1; shift ;;
  esac
done
if [ -z "$dir" ]; then
  echo "usage: rotate.sh [--dry-run] [--keep N] DIR" >&2
  exit 2
fi
cd $dir || exit 1
for prefix in $(ls *.tar.zst | sed 's/-.*//' | sort -u); do
  n=0
  for f in $(ls -t $prefix-*.tar.zst); do
    n=$((n + 1))
    if [ $n -gt $keep ]; then
      if [ "$DRYRUN" = 1 ]; then
        echo "would delete $f"
      else
        rm -f $f
        echo "deleted $f"
      fi
    fi
  done
done
