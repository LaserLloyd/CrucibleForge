#!/bin/bash
# lockrun.sh [-w SECONDS] NAME -- COMMAND [ARG...]
wait_s=0
if [ "$1" = "-w" ]; then
  wait_s=$2
  shift 2
fi
name=$1
shift
[ "$1" = "--" ] && shift
dir=${LOCKDIR:-/tmp/lockrun}
mkdir -p $dir
exec 9>$dir/$name.lock
if ! flock -n 9; then
  echo "lockrun: $name is busy" >&2
  exit 1
fi
$* &
echo $! > $dir/$name.pid
wait
rm -f $dir/$name.pid
