#!/bin/bash
# rotate.sh [--dry-run] [--keep N] DIR : keep the newest N backups per prefix
set -u
export LC_ALL=C
usage() { echo "usage: rotate.sh [--dry-run] [--keep N] DIR" >&2; exit 2; }

keep=3
dry_run=0
dir=
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift ;;
    --keep)
      [ $# -ge 2 ] || usage
      keep=$2; shift 2 ;;
    -*) usage ;;
    *) [ -z "$dir" ] || usage; dir=$1; shift ;;
  esac
done
[ -n "$dir" ] || usage
case "$keep" in ''|*[!0-9]*) usage ;; esac
[ "$keep" -ge 1 ] || usage
if [ ! -d "$dir" ]; then
  echo "rotate.sh: not a directory: $dir" >&2
  exit 3
fi

# "<stamp>\t<prefix>\t<name>" for every matching file, newest first per prefix
list=$(
  for path in "$dir"/*; do
    [ -f "$path" ] || continue
    name=${path##*/}
    case "$name" in
      *-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].tar.zst) ;;
      *) continue ;;
    esac
    base=${name%.tar.zst}
    stamp=${base: -15}
    prefix=${base%-*-*}
    [ -n "$prefix" ] || continue
    printf '%s\t%s\t%s\n' "$prefix" "$stamp" "$name"
  done | sort -t "$(printf '\t')" -k1,1 -k2,2r
)

victims=$(
  printf '%s\n' "$list" | awk -F '\t' -v keep="$keep" 'NF == 3 { n[$1]++; if (n[$1] > keep) print $3 }' | sort
)
[ -n "$victims" ] || exit 0
while IFS= read -r name; do
  if [ "${DRYRUN:-0}" = 1 ]; then
    echo "would delete $name"
  else
    rm -f -- "$dir/$name"
    echo "deleted $name"
  fi
done <<< "$victims"
