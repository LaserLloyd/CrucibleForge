#!/bin/bash
# lockrun.sh [-w SECONDS] NAME -- COMMAND [ARG...]
usage() { echo "usage: lockrun.sh [-w SECONDS] NAME -- COMMAND [ARG...]" >&2; exit 64; }

wait_s=
if [ "${1-}" = "-w" ]; then
  [ $# -ge 2 ] || usage
  wait_s=$2
  case "$wait_s" in ''|*[!0-9]*) usage ;; esac
  shift 2
fi
[ $# -ge 1 ] || usage
name=$1
shift
case "$name" in ''|*[!A-Za-z0-9._-]*) usage ;; esac
[ "${1-}" = "--" ] || usage
shift
[ $# -ge 1 ] || usage

dir=${LOCKDIR:-/tmp/lockrun}
mkdir -p -- "$dir" || exit 1
lock="$dir/$name.lock"
pidfile="$dir/$name.pid"

exec 9>"$lock" || exit 1
if [ -n "$wait_s" ]; then
  flock -w "$wait_s" 9
else
  flock -n 9
fi
if [ $? -ne 0 ]; then
  echo "lockrun: $name is busy" >&2
  exit 75
fi

child=
got=
cleanup() { rm -f -- "$pidfile"; }
forward() {
  got=$1
  [ -n "$child" ] && kill "-$2" "$child" 2>/dev/null
}
trap 'forward 143 TERM' TERM
trap 'forward 130 INT' INT
trap cleanup EXIT

(trap - INT QUIT; exec "$@") &
child=$!
echo "$child" > "$pidfile"
while :; do
  wait "$child"
  rc=$?
  # wait returns early (>128) when a trapped signal arrives; keep waiting
  # until the child is really gone
  kill -0 "$child" 2>/dev/null || break
done
if [ -n "$got" ]; then
  exit "$got"
fi
exit "$rc"
