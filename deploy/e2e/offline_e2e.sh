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
# the two fake servers listen on PORT and PORT+1: well away from the ports model servers use (1234, 8000, 8080,
# 11434) and the ranges their workers take (18100-18199 seen on a Windows rig)
PORT=${PORT:-28099}
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

# a native Windows python (Git Bash) needs C:/... paths: MSYS path conversion is off on purpose (see above)
FAKEPY="$(hostpath "$HERE")/fake_openai.py"
LPORT=$((PORT + 1)); SPORT=$((PORT + 2)); VPORT=$((PORT + 3))  # VPORT: the results page, published from the image
for p in "$PORT" "$LPORT" "$SPORT" "$VPORT"; do
    # SO_REUSEADDR off Windows: a port a previous run's page just closed sits in TIME_WAIT, which is not "in use" for a
    # server (they all set it); on Windows the same option would let the probe bind a port that IS in use
    if ! "$PY" -c "import socket,sys; s=socket.socket(); sys.argv[2] or s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', int(sys.argv[1]))); s.close()" "$p" "$MSYS" 2>/dev/null; then
        echo "E2E STOP: port $p is already in use on this computer (the fake servers and the results page need PORT to PORT+3)."
        echo "  Pick two free ones: PORT=<n> sh deploy/e2e/offline_e2e.sh  (now PORT=$PORT)"
        rm -rf "$WORK"; exit 2
    fi
done
"$PY" "$FAKEPY" "$PORT" "$(hostpath "$LOG")" & FPID=$!
# a second fake: a local model server that asks for a key (StudioForge, vLLM --api-key, llama-server --api-key)
LKEY=local-e2e-key-0001
FAKE_REQUIRE_KEY=$LKEY FAKE_CTX=262144 "$PY" "$FAKEPY" "$LPORT" "$(hostpath "$LOG.local")" & LPID=$!
# a third: a server with ONE slot that queues the rest silently and streams each answer over 1 s (llama-server -np 1)
FAKE_SLOTS=1 FAKE_HOLD_S=1 FAKE_CTX=262144 "$PY" "$FAKEPY" "$SPORT" "$(hostpath "$LOG.slots")" & SPID=$!
VOL=cf-e2e-$$
trap 'kill $FPID $LPID $SPID 2>/dev/null; $ENGINE rm -f "cf-e2e-view-$$" "cf-e2e-serve-$$" >/dev/null 2>&1; $ENGINE volume rm -f "$VOL" >/dev/null 2>&1; [ -n "${KEEP:-}" ] || rm -rf "$WORK" 2>/dev/null' EXIT
sleep 1

FAKE="http://$HOSTNAME_IN:$PORT/api/v1"
run() {  # run KEY DIR ARGS... : the documented one command, plus the fake OpenRouter address
    key=$1; dir=$2; shift 2
    # shellcheck disable=SC2086
    $ENGINE run --rm $ADDHOST -e OPENROUTER_API_KEY="$key" -e CRUCIBLEFORGE_OPENROUTER_URL="$FAKE" ${RUNENV:-} \
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
    out=$(runm "--uidmap 0:1:65536 --gidmap 0:1:65536 -e CF_ASSUME_REAL_ROOT=1 -v $(hostpath "$WORK/ign"):/data$Z" \
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
[ $rc -eq 21 ] && echo "$out" | grep -q "an Anthropic key" && ok "another service's key: rc 21" || fail "other key: rc $rc"
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
# a 1-slot server: check reads its slots; a run with 4 requests in flight (3 wait in its queue, silent, longer than
# the 2 s stall limit set here) must finish without a stall: queue time is not silence (4.5.1, the rig's StudioForge)
SL="--local http://$HOSTNAME_IN:$SPORT --judge-local http://$HOSTNAME_IN:$SPORT fake-judge"
mkdir -p "$WORK/s"
# shellcheck disable=SC2086
out=$($ENGINE run --rm $ADDHOST -v "$(hostpath "$WORK/s"):/data$Z" "$IMAGE" check fake-model $SL 2>&1); rc=$?
[ $rc -eq 0 ] && echo "$out" | grep -q "requests: 1 at a time (llama-server /props)" \
    && ok "check reads the server's slots: 1 at a time (llama-server /props)" || fail "slots check: rc $rc: $out"
# shellcheck disable=SC2086
out=$($ENGINE run --rm $ADDHOST -e CF_STALL_TIMEOUT_S=2 -v "$(hostpath "$WORK/s"):/data$Z" "$IMAGE" \
      test fake-model $SL --smoke --yes --parallel 4 2>&1); rc=$?
q=$(curl -s "http://127.0.0.1:$SPORT/stats" 2>/dev/null | "$PY" -c "import json,sys; print(json.load(sys.stdin)['queued_max'])" 2>/dev/null)
if [ $rc -eq 0 ] && ! echo "$out" | grep -q "no data from the server\|no answer started\|stopped answering" \
    && [ "${q:-0}" -ge 2 ]; then
    ok "1-slot server, 4 at once, 2 s stall limit: up to $q queued, no stall, rc 0"
else fail "slotted run: rc $rc, queued ${q:-?}: $(echo "$out" | tail -n 15)"; fi
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
# the folder's seeded local provider (and its local judges) point at the 1-slot fake: a run that uses only hosted
# models must not send it a single request
hits() { curl -s "http://127.0.0.1:$SPORT/stats" 2>/dev/null | "$PY" -c "import json,sys; print(json.load(sys.stdin)['requests'])" 2>/dev/null; }
h0=$(hits)
RUNENV="-e LLM_BASE_URL=http://$HOSTNAME_IN:$SPORT/v1"
out=$(run sk-e2e "$WORK/r" test fake/fake-model --yes 2>&1); rc=$?
echo "$out" > "$WORK/run1.txt"
h1=$(hits)
[ -n "$h0" ] && [ "$h0" = "$h1" ] && grep -q "host.containers.internal:$SPORT\|host.docker.internal:$SPORT\|$HOSTNAME_IN:$SPORT" "$WORK/r/models.yaml" \
    && ok "hosted-only run: 0 requests to the local server in models.yaml" || fail "hosted-only run sent $h0 -> $h1 requests to the local server: $(curl -s "http://127.0.0.1:$SPORT/stats" | cut -c1-300)"
grep -qi "studioforge" "$WORK/run1.txt" && fail "a hosted run printed StudioForge: $(grep -i studioforge "$WORK/run1.txt" | head -2)" \
    || ok "nothing StudioForge-specific printed"
grep -qE "^ *20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9:]{8}[,.][0-9]+ (INFO|WARNING|ERROR)" "$WORK/run1.txt" \
    && fail "raw log lines on the terminal: $(grep -cE ' (INFO|WARNING) ' "$WORK/run1.txt")" || ok "no raw log lines on the terminal"
grep -qE "^  [0-9]+ of [0-9]+ answers" "$WORK/run1.txt" && ok "progress: $(grep -E '^  [0-9]+ of [0-9]+ answers' "$WORK/run1.txt" | tail -1)" \
    || fail "no progress line"
grep -q " INFO " "$WORK/r/results/run.log" 2>/dev/null && ok "the raw log is in results/run.log" || fail "no results/run.log"
[ $rc -eq 0 ] && ok "run: rc 0" || fail "run: rc $rc: $(tail -n 30 "$WORK/run1.txt")"
grep -q "^Overall " "$WORK/run1.txt" && ok "summary: $(grep '^Overall' "$WORK/run1.txt")" || fail "no Overall line"
grep -q "^Code " "$WORK/run1.txt" && grep -q "^Chat " "$WORK/run1.txt" && ok "Code and Chat lines" || fail "no Code/Chat lines"
grep -q "^Adult (NSFW+" "$WORK/run1.txt" && ok "adult line" || fail "no adult line"
grep -q "^Cost of this run: model \$[0-9.]* + judge \$0\.0*[1-9]" "$WORK/run1.txt" && ok "$(grep '^Cost of this run' "$WORK/run1.txt")" \
    || fail "no cost line with a non-zero judge cost"
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
echo "$out" > "$WORK/run2.txt"
[ "$(hits)" = "$h0" ] && ok "same command again: still 0 requests to the local server" || fail "resume asked the local server"
grep -q "in this folder cost " "$WORK/run2.txt" && ok "$(grep 'in this folder cost' "$WORK/run2.txt" | sed 's/^ *//')" \
    || fail "no folder total on the second run: $(grep -i cost "$WORK/run2.txt" | tail -2)"
RUNENV=""
t2=$(cat "$WORK"/r/results/transcripts_*.jsonl 2>/dev/null | wc -l)
gen=$(tail -n +"$((n1 + 1))" "$LOG" | grep -c '"model": "fake/fake-model"')
# the same command again re-uses every stored answer: no new rows; the only model calls are the run's few
# link and speed probes
[ $rc -eq 0 ] && [ "$t2" -eq "$t1" ] && [ "$gen" -le 8 ] \
    && ok "same command again: rc 0, no new answers ($t1 rows kept, $gen probe calls)" \
    || fail "resume: rc $rc, rows $t1 -> $t2, $gen model requests"

# 4. the results page: `view` on the finished folder, published on loopback as the docs say; only the report is
#    reachable; then `test --serve` on the same (complete) folder keeps the page open until it is stopped
page() { curl -s --path-as-is -o "$WORK/page.out" -D "$WORK/page.head" -w '%{http_code}' -X "${2:-GET}" "http://127.0.0.1:$VPORT$1"; }
waitpage() { i=0; while [ $i -lt 60 ]; do [ "$(page /report.md)" = 200 ] && return 0; sleep 1; i=$((i + 1)); done; return 1; }
$ENGINE run -d --name "cf-e2e-view-$$" -p "127.0.0.1:$VPORT:8377" -v "$(hostpath "$WORK/r"):/data$Z" "$IMAGE" view >/dev/null
if waitpage; then
    [ "$(page /)" = 200 ] && cmp -s "$WORK/page.out" "$WORK/r/results/report.html" && ok "view: / is the report (200)" \
        || fail "view: / is not the report"
    grep -qi "^content-security-policy: default-src 'none'.*script-src 'sha256-" "$WORK/page.head" \
        && ! grep -qi "^access-control" "$WORK/page.head" && ok "view: strict CSP (script by hash), no CORS" \
        || fail "view headers: $(tr -d '\r' < "$WORK/page.head" | tr '\n' '|')"
    bad=""
    for u in /models.yaml /../models.yaml /%2e%2e/models.yaml /..%2f..%2fetc%2fpasswd "/$(cd "$WORK/r/results" && ls transcripts_*.jsonl | head -1)" \
             /results/report.html /spend/ /report.json /.test-complete.json; do
        c=$(page "$u"); [ "$c" = 404 ] || bad="$bad $u=$c"
    done
    [ -z "$bad" ] && ok "view: models.yaml, transcripts, traversal and other paths are 404" || fail "view served:$bad"
    [ "$(page / POST)" = 405 ] && ok "view: read only (POST 405)" || fail "view: POST not refused"
else
    fail "view: no page on 127.0.0.1:$VPORT: $($ENGINE logs "cf-e2e-view-$$" 2>&1 | tail -5)"
fi
$ENGINE logs "cf-e2e-view-$$" 2>&1 | grep -q "Your results are open at http://localhost:8377 (press Ctrl-C to close)" \
    && ok "view: says where the page is" || fail "view: no address line"
$ENGINE rm -f "cf-e2e-view-$$" >/dev/null 2>&1
# the jail with the page up in the same container: graded code reaches no network, this port included
out=$(runm "-v $(hostpath "$WORK/r"):/data$Z" sh -c 'python -m crucibleforge.viewserver >/dev/null & sleep 2;
  python -c "import socket; socket.create_connection((\"127.0.0.1\", 8377), 2); print(\"PAGE-UP\")";
  python -m crucibleforge.jail --selftest' 2>&1); rc=$?
echo "$out" | grep -q PAGE-UP && echo "$out" | grep -q '"ok": true' && echo "$out" | grep -q '"net_results_page": "' \
    && ok "jail selftest with the page up: ok, the page's port is unreachable from graded code" \
    || fail "jail with page: rc $rc: $(echo "$out" | tail -3 | cut -c1-300)"
$ENGINE run -d --name "cf-e2e-serve-$$" $ADDHOST -e OPENROUTER_API_KEY=sk-e2e -e CRUCIBLEFORGE_OPENROUTER_URL="$FAKE" \
    -p "127.0.0.1:$VPORT:8377" -v "$(hostpath "$WORK/r"):/data$Z" "$IMAGE" test fake/fake-model --yes --serve >/dev/null
i=0; until $ENGINE logs "cf-e2e-serve-$$" 2>&1 | grep -q "Your results are open at" || [ $i -ge 900 ]; do sleep 1; i=$((i + 1)); done
$ENGINE logs "cf-e2e-serve-$$" 2>&1 > "$WORK/serve.txt"
grep -q "Every answer is already in this folder" "$WORK/serve.txt" && ! grep -q "working out the questions" "$WORK/serve.txt" \
    && ok "third run: says every answer is in before any planning" || fail "third run planned: $(grep -i 'plan' "$WORK/serve.txt" | head -3)"
grep -q "Your results are open at http://localhost:8377" "$WORK/serve.txt" && [ "$(page /)" = 200 ] \
    && cmp -s "$WORK/page.out" "$WORK/r/results/report.html" \
    && ok "test --serve: says the results are open and serves the report until stopped" \
    || fail "test --serve: $($ENGINE logs "cf-e2e-serve-$$" 2>&1 | tail -4)"
$ENGINE stop -t 20 "cf-e2e-serve-$$" >/dev/null 2>&1
src=$($ENGINE inspect -f '{{.State.ExitCode}}' "cf-e2e-serve-$$" 2>/dev/null)
[ "$src" = 0 ] && ok "test --serve: stopping it closes the page with the run's code (0)" || fail "test --serve exit $src"
$ENGINE rm -f "cf-e2e-serve-$$" >/dev/null 2>&1

[ $fails -eq 0 ] && echo "E2E PASS" || echo "E2E FAIL ($fails)"
exit $fails
