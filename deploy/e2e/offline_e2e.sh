#!/bin/sh
# Offline end-to-end check of the image, exactly as a first-time user runs it, with a fake provider (no key, no
# spending): the one-command `test` run, its plain failures, the jail selftest, file ownership, and resume.
#
#   ENGINE=docker|podman IMAGE=<image> sh deploy/e2e/offline_e2e.sh
#
# Exit 0 when every check passes; each failed check prints "FAIL: ...". Needs python3 on the host.
set -u
ENGINE=${ENGINE:-docker}
IMAGE=${IMAGE:-crucibleforge:dev}
PORT=${PORT:-18099}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d "${E2E_TMP:-/var/tmp}/cf-e2e-XXXXXX")
LOG="$WORK/requests.jsonl"
fails=0
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }
ok() { echo "ok:   $*"; }

case "$ENGINE" in
    podman) HOSTNAME_IN=host.containers.internal; ADDHOST="" ;;
    *)      HOSTNAME_IN=host.docker.internal; ADDHOST="--add-host=host.docker.internal:host-gateway" ;;
esac
# Rootless Docker cannot reach the host through host-gateway (its network namespace hides the host's loopback and
# bridge); E2E_HOST=<this machine's network address> points the container at the fake provider there instead.
if [ -n "${E2E_HOST:-}" ]; then HOSTNAME_IN=$E2E_HOST; ADDHOST=""; fi
Z=""
[ -e /sys/fs/selinux/enforce ] && [ "$(cat /sys/fs/selinux/enforce 2>/dev/null)" = "1" ] && Z=":Z"

python3 "$HERE/fake_openai.py" "$PORT" "$LOG" & FPID=$!
trap 'kill $FPID 2>/dev/null; [ -n "${KEEP:-}" ] || rm -rf "$WORK" 2>/dev/null' EXIT
sleep 1

FAKE="http://$HOSTNAME_IN:$PORT/api/v1"
run() {  # run KEY DIR ARGS... : the documented one command, plus the fake OpenRouter address
    key=$1; dir=$2; shift 2
    # shellcheck disable=SC2086
    $ENGINE run --rm $ADDHOST -e OPENROUTER_API_KEY="$key" -e CRUCIBLEFORGE_OPENROUTER_URL="$FAKE" \
        -v "$dir:/data$Z" "$IMAGE" "$@"
}

# 1. plain failures, each before anything is spent
mkdir -p "$WORK/f"
out=$(run "" "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 20 ] && echo "$out" | grep -q "No OpenRouter key" && ok "no key: rc 20" || fail "no key: rc $rc: $out"
out=$(run sk-rejected "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 21 ] && echo "$out" | grep -q "rejected the key" && ok "rejected key: rc 21" || fail "rejected key: rc $rc"
out=$(run sk-broke "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 22 ] && echo "$out" | grep -q "out of credit" && ok "no credit: rc 22" || fail "no credit: rc $rc"
out=$(run sk-e2e "$WORK/f" test fake/fake-modle --yes 2>&1); rc=$?
[ $rc -eq 23 ] && echo "$out" | grep -q "fake/fake-model" && ok "unknown model: rc 23, suggests the id" || fail "unknown model: rc $rc"
out=$(run sk-e2e "$WORK/f" test fake/fake-model </dev/null 2>&1); rc=$?
[ $rc -eq 25 ] && echo "$out" | grep -q -- "--yes" && ok "no terminal: rc 25, names --yes" || fail "no terminal: rc $rc"

# 2. the hostile program, through the real grader path, in the image
mkdir -p "$WORK/j"
out=$($ENGINE run --rm $ADDHOST -e OPENROUTER_API_KEY=sk-canary-e2e -e CF_SELFTEST_HOST=$HOSTNAME_IN \
      -v "$WORK/j:/data$Z" "$IMAGE" python -m crucibleforge.jail --selftest 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q '"ok": true' && ok "jail selftest" || fail "jail selftest rc $rc: $out"

# 3. one whole run with the one command, then the same command again (resume: nothing new to send)
mkdir -p "$WORK/r"
out=$(run sk-e2e "$WORK/r" test fake/fake-model --yes 2>&1); rc=$?
echo "$out" > "$WORK/run1.txt"
[ $rc -eq 0 ] && ok "run: rc 0" || fail "run: rc $rc: $(tail -n 30 "$WORK/run1.txt")"
grep -q "^Overall " "$WORK/run1.txt" && ok "summary: $(grep '^Overall' "$WORK/run1.txt")" || fail "no Overall line"
grep -q "^Code " "$WORK/run1.txt" && grep -q "^Chat " "$WORK/run1.txt" && ok "Code and Chat lines" || fail "no Code/Chat lines"
grep -q "^Adult (NSFW+" "$WORK/run1.txt" && ok "adult line" || fail "no adult line"
grep -q "^Judge cost: \$0\.0*[1-9]" "$WORK/run1.txt" && ok "$(grep '^Judge cost' "$WORK/run1.txt")" || fail "no non-zero judge cost line"
grep -q "report.html" "$WORK/run1.txt" && ok "names report.html" || fail "does not say which file to open"
[ -s "$WORK/r/results/report.html" ] && [ -s "$WORK/r/results/report.md" ] && ok "report files on the host" || fail "no report files"
owner=$(stat -c %u "$WORK/r/results/report.html" 2>/dev/null)
[ "$owner" = "$(id -u)" ] && ok "files belong to the user ($owner)" || fail "report.html owned by $owner, not $(id -u)"
[ ! -e "$WORK/r/PWNED" ] && [ ! -e "$WORK/r/leak-probe.txt" ] && [ ! -e "$WORK/r/results/leak-report.json" ] \
    && ok "model code wrote nothing into the folder" || fail "model code wrote into the folder"
grep -q '"model": "openrouter~' "$LOG" && fail "a request named the judge's internal provider" || ok "no internal provider names on the wire"
n1=$(wc -l < "$LOG")
t1=$(cat "$WORK"/r/results/transcripts_*.jsonl 2>/dev/null | wc -l)
out=$(run sk-e2e "$WORK/r" test fake/fake-model --yes 2>&1); rc=$?
t2=$(cat "$WORK"/r/results/transcripts_*.jsonl 2>/dev/null | wc -l)
gen=$(tail -n +"$((n1 + 1))" "$LOG" | grep -c '"model": "fake/fake-model"')
# the same command again re-uses every stored answer: no new rows; the only model calls are the run's few
# link and speed probes
[ $rc -eq 0 ] && [ "$t2" -eq "$t1" ] && [ "$gen" -le 8 ] \
    && ok "same command again: rc 0, no new answers ($t1 rows kept, $gen probe calls)" \
    || fail "resume: rc $rc, rows $t1 -> $t2, $gen model requests"

[ $fails -eq 0 ] && echo "E2E PASS" || echo "E2E FAIL ($fails)"
exit $fails
