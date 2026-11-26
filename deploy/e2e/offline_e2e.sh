#!/bin/sh
# Offline end-to-end check of the image, exactly as a first-time user runs it, with a fake provider (no key, no
# spending): the one-command `test` run, its plain failures, the jail selftest, file ownership, and resume.
#
#   ENGINE=docker|podman IMAGE=<image> sh deploy/e2e/offline_e2e.sh
#
# Exit 0 when every check passes; each failed check prints "FAIL: ...". Needs python3 (or python) on the host.
# Runs under Git Bash on Windows too (Docker Desktop): MSYS path conversion is turned off and folders are passed
# in C:/... form; the file-owner check, which means nothing on a Windows folder, is skipped there.
set -u
ENGINE=${ENGINE:-docker}
IMAGE=${IMAGE:-crucibleforge:dev}
PORT=${PORT:-18099}
HERE=$(cd "$(dirname "$0")" && pwd)
MSYS=""
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) MSYS=1; export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' ;; esac
PY=$(command -v python3 || command -v python)
DEFTMP=/var/tmp; [ -n "$MSYS" ] && DEFTMP=${TEMP:-/tmp}
WORK=$(mktemp -d "${E2E_TMP:-$DEFTMP}/cf-e2e-XXXXXX")
# the form the container engine wants for a host folder (C:/Users/... under Git Bash)
hostpath() { if [ -n "$MSYS" ]; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
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

"$PY" "$HERE/fake_openai.py" "$PORT" "$LOG" & FPID=$!
# a second fake: a local model server that asks for a key (StudioForge, vLLM --api-key, llama-server --api-key)
LPORT=$((PORT + 1)); LKEY=local-e2e-key-0001
FAKE_REQUIRE_KEY=$LKEY FAKE_CTX=262144 "$PY" "$HERE/fake_openai.py" "$LPORT" "$LOG.local" & LPID=$!
VOL=cf-e2e-$$
trap 'kill $FPID $LPID 2>/dev/null; $ENGINE volume rm -f "$VOL" >/dev/null 2>&1; [ -n "${KEEP:-}" ] || rm -rf "$WORK" 2>/dev/null' EXIT
sleep 1

FAKE="http://$HOSTNAME_IN:$PORT/api/v1"
run() {  # run KEY DIR ARGS... : the documented one command, plus the fake OpenRouter address
    key=$1; dir=$2; shift 2
    # shellcheck disable=SC2086
    $ENGINE run --rm $ADDHOST -e OPENROUTER_API_KEY="$key" -e CRUCIBLEFORGE_OPENROUTER_URL="$FAKE" \
        -v "$(hostpath "$dir"):/data$Z" "$IMAGE" "$@"
}
# the same, with any results folder spec (a tmpfs, a named volume) and extra engine options
runm() {  # runm "MOUNT OPTIONS" ARGS... ; with no key, `test` stops right after the folder step (rc 20)
    m=$1; shift
    # shellcheck disable=SC2086
    $ENGINE run --rm $ADDHOST -e CRUCIBLEFORGE_OPENROUTER_URL="$FAKE" $m "$IMAGE" "$@"
}
SELFTEST="python -m crucibleforge.jail --selftest"

# 0. the results folder, as Docker Desktop on Windows shows a fresh one (owned by root, mode 777): the first run
#    must work, the folder must belong to the run user afterwards, and the graded code's user must not write it.
#    CF_ASSUME_REAL_ROOT=1 takes the rootful path on a rootless engine (no effect under rootful Docker).
T="--mount type=tmpfs,destination=/data,tmpfs-mode=0777 -e CF_ASSUME_REAL_ROOT=1"
out=$(runm "$T" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 20 ] && echo "$out" | grep -q "PASS  folder" && ok "fresh 0:0 777 folder: first run passes the folder step" \
    || fail "fresh 0:0 777 folder: rc $rc: $out"
out=$(runm "$T" sh -c 'stat -c "%u %a" /data; id -u' 2>&1)
[ "$(echo "$out" | tail -2 | tr '\n' ' ')" = "1000 770 1000 " ] && ok "fresh 0:0 777 folder: now 1000 770, run as 1000" \
    || fail "fresh 0:0 777 folder: $out"
out=$(runm "$T" $SELFTEST 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q '"ok": true' && ok "fresh 0:0 777 folder: jail selftest" || fail "777 selftest rc $rc"
# the same, not empty (a file the person dropped in), on a named volume, twice
$ENGINE volume create "$VOL" >/dev/null
$ENGINE run --rm -v "$VOL:/data" --entrypoint sh "$IMAGE" -c 'touch /data/notes.txt; chown 0:0 /data; chmod 777 /data'
V="-v $VOL:/data -e CF_ASSUME_REAL_ROOT=1"
for i in 1 2; do
    out=$(runm "$V" test fake/fake-model --yes 2>&1); rc=$?
    [ $rc -eq 20 ] && echo "$out" | grep -q "PASS  folder" && ok "non-empty 0:0 777 folder, run $i: folder step passes" \
        || fail "non-empty 777 run $i: rc $rc: $out"
done
out=$(runm "$V" $SELFTEST 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q '"ok": true' && ok "non-empty 0:0 777 folder: jail selftest" || fail "volume selftest rc $rc"
# a file system that ignores ownership changes (Docker Desktop without metadata, SMB): podman can show it with a
# user-namespace map that leaves the folder's owner unmapped; the run must work and say the folder is shared
if [ "$ENGINE" = "podman" ] && [ -z "$MSYS" ]; then
    mkdir -p "$WORK/ign"; chmod 777 "$WORK/ign"
    out=$(runm "--uidmap 0:1:65536 --gidmap 0:1:65536 -e CF_ASSUME_REAL_ROOT=1 -v $WORK/ign:/data$Z" \
          test fake/fake-model --yes 2>&1); rc=$?
    [ $rc -eq 20 ] && echo "$out" | grep -q "does not keep owners apart" \
        && ok "folder that ignores chown: runs, says the folder is shared" || fail "ignored chown: rc $rc: $out"
fi
# no -v at all: refused before anything, with the command for each shell
out=$($ENGINE run --rm -e OPENROUTER_API_KEY=sk-e2e "$IMAGE" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 28 ] && echo "$out" | grep -q "PowerShell" && ok "no -v: rc 28, shows the command" || fail "no -v: rc $rc: $out"

# 1. plain failures, each before anything is spent
mkdir -p "$WORK/f"
out=$($ENGINE run --rm -v "$(hostpath "$WORK/f"):/data$Z" "$IMAGE" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 20 ] && echo "$out" | grep -q "No OpenRouter key" && ok "no key: rc 20" || fail "no key: rc $rc: $out"
out=$(run "" "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 20 ] && echo "$out" | grep -q "key is empty" && ok "empty key: rc 20" || fail "empty key: rc $rc: $out"
out=$(run '"sk-or-v1-quoted"' "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 21 ] && echo "$out" | grep -q "quote marks" && ok "quoted key: rc 21" || fail "quoted key: rc $rc: $out"
out=$(run sk-ant-e2e "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 21 ] && echo "$out" | grep -q "Anthropic key" && ok "another service's key: rc 21" || fail "other key: rc $rc"
out=$(run sk-busy "$WORK/f" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 30 ] && echo "$out" | grep -q "nothing is lost" && ok "rate limited: rc 30" || fail "429: rc $rc: $out"
out=$($ENGINE run --rm -e OPENROUTER_API_KEY=sk-e2e -e CRUCIBLEFORGE_OPENROUTER_URL=http://no-such-host.invalid/api/v1 \
      -v "$(hostpath "$WORK/f"):/data$Z" "$IMAGE" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 29 ] && echo "$out" | grep -q "HTTPS_PROXY" && ok "no DNS: rc 29, names HTTPS_PROXY" || fail "DNS: rc $rc: $out"
out=$(run sk-e2e "$WORK/f" test fake/fake-model --local http://127.0.0.1:9 --judge-local http://127.0.0.1:9 j --yes 2>&1); rc=$?
[ $rc -eq 26 ] && echo "$out" | grep -q "0.0.0.0" && ok "local server down: rc 26" || fail "local: rc $rc: $out"
LOCAL="--local http://$HOSTNAME_IN:$LPORT --judge-local http://$HOSTNAME_IN:$LPORT fake-judge"
mkdir -p "$WORK/l"
# shellcheck disable=SC2086
out=$(run sk-e2e "$WORK/l" check fake-model $LOCAL 2>&1); rc=$?
[ $rc -eq 37 ] && echo "$out" | grep -q "FIX   model" && echo "$out" | grep -q -- "-e LOCAL_API_KEY=" \
    && ok "local server wants a key, none given: rc 37" || fail "local no key: rc $rc: $out"
# shellcheck disable=SC2086
out=$($ENGINE run --rm $ADDHOST -e LOCAL_API_KEY=wrong-e2e-key -v "$(hostpath "$WORK/l"):/data$Z" "$IMAGE" \
      check fake-model $LOCAL 2>&1); rc=$?
[ $rc -eq 38 ] && echo "$out" | grep -q "rejected the key in LOCAL_API_KEY" && ! echo "$out" | grep -q wrong-e2e-key \
    && ok "local server rejects the key: rc 38, key not printed" || fail "local wrong key: rc $rc: $out"
# shellcheck disable=SC2086
out=$($ENGINE run --rm $ADDHOST -e LOCAL_API_KEY=$LKEY -v "$(hostpath "$WORK/l"):/data$Z" "$IMAGE" \
      check fake-mdoel $LOCAL 2>&1); rc=$?
[ $rc -eq 23 ] && echo "$out" | grep -q "^ *fake-model$" && ok "local wrong id: rc 23, lists the server's ids" \
    || fail "local wrong id: rc $rc: $out"
# shellcheck disable=SC2086
out=$($ENGINE run --rm $ADDHOST -e LOCAL_API_KEY=$LKEY -v "$(hostpath "$WORK/l"):/data$Z" "$IMAGE" \
      check fake-model $LOCAL 2>&1); rc=$?
if [ $rc -eq 0 ] && echo "$out" | grep -q "PASS  model.*context window 262144" && echo "$out" | grep -q "PASS  judge" \
    && echo "$out" | grep -q "every question in this suite fits" && ! echo "$out" | grep -q "$LKEY" \
    && grep -q "api_key_env: LOCAL_API_KEY" "$WORK/l/models.yaml" && ! grep -rq "$LKEY" "$WORK/l" 2>/dev/null; then
    ok "local server with the right key: check rc 0, context reported, key only by name in models.yaml"
else fail "local right key: rc $rc: $out"; fi
mkdir -p "$WORK/c"; printf 'providers:\n  openrouter: {type: openai\n' > "$WORK/c/models.yaml"
out=$(run sk-e2e "$WORK/c" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 31 ] && echo "$out" | grep -q "line" && ok "broken models.yaml: rc 31, names the line" || fail "config: rc $rc: $out"
mkdir -p "$WORK/n"; echo "9.9.9-future" > "$WORK/n/.image-version"
out=$(run sk-e2e "$WORK/n" test fake/fake-model --yes 2>&1); rc=$?
[ $rc -eq 35 ] && ok "folder from a newer image: rc 35" || fail "newer folder: rc $rc: $out"
out=$(run sk-e2e "$WORK/f" check fake/fake-modle 2>&1); rc=$?
[ $rc -eq 23 ] && echo "$out" | grep -q "FIX   model" && ok "check: a FIX line for a wrong id, rc 23" || fail "check bad: rc $rc: $out"
out=$(run sk-e2e "$WORK/f" check fake/fake-model 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q "PASS  plan" && echo "$out" | grep -q "^Ready" && ok "check: all PASS, rc 0" \
    || fail "check: rc $rc: $out"
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
out=$($ENGINE run --rm $ADDHOST -e OPENROUTER_API_KEY=sk-canary-e2e -e LOCAL_API_KEY=local-canary-e2e \
      -e JUDGE_LOCAL_API_KEY=judge-canary-e2e -e CF_SELFTEST_HOST=$HOSTNAME_IN \
      -v "$(hostpath "$WORK/j"):/data$Z" "$IMAGE" python -m crucibleforge.jail --selftest 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q '"ok": true' && echo "$out" | grep -q '"named_keys_in_env": \[\]' \
    && echo "$out" | grep -q '"named_keys_in_proc": \[\]' \
    && ok "jail selftest (OpenRouter, local and judge keys out of the graded code's env and /proc)" \
    || fail "jail selftest rc $rc: $out"

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
if [ -n "$MSYS" ]; then
    ok "files belong to the user: skipped (a Windows folder has no Unix owner; check it in Explorer)"
else
    owner=$(stat -c %u "$WORK/r/results/report.html" 2>/dev/null)
    [ "$owner" = "$(id -u)" ] && ok "files belong to the user ($owner)" || fail "report.html owned by $owner, not $(id -u)"
fi
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
