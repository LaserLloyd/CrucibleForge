#!/bin/sh
# CrucibleForge container entrypoint. Deliberately POSIX sh and short.
#
# In order:
#  1. If started as root: align the `app` account with PUID/PGID, chown /data,
#     drop privileges (setpriv) and re-exec. Not root: skip (nothing to adjust).
#  2. Fail LOUD and EARLY when /data is not writable (bind-mount UID mismatch).
#  3. Pre-upgrade backup: when the image version differs from the one that last
#     ran on this volume, copy the small state files (models.yaml, the results
#     summaries) to /data/backups/<old>-to-<new>-<timestamp>/ (keep the last 5).
#     Transcripts (transcripts_*.jsonl, the large files) are NOT copied; back
#     those up with the volume backup in docs/deploy/docker.md.
#  4. First start: seed /data/models.yaml from CRUCIBLEFORGE_SEED_TEMPLATE
#     (default /app/models.example.yaml; the beginner compose.yaml picks the
#     short /app/deploy/models.docker.yaml) with the local provider pointed at
#     the Docker host. An existing file is never touched.
#  5. `docker compose run --rm app status` works: a first argument that is not
#     an executable (or starts with "-") is a crucibleforge subcommand.
#  6. CRUCIBLEFORGE_GUI_TOKEN, when set, is the GUI's stable access token (read by the app
#     from the environment; a stored /data/gui_token is used when it is empty).
#
# It does not install, migrate or "repair" anything, and never recursively
# chowns a healthy volume on every boot.
set -eu

DATA_DIR=/data
CFG="${CRUCIBLEFORGE_CONFIG:-$DATA_DIR/models.yaml}"
LLM_BASE_URL="${LLM_BASE_URL:-http://host.docker.internal:1234/v1}"
SEED_TEMPLATE="${CRUCIBLEFORGE_SEED_TEMPLATE:-/app/models.example.yaml}"
JAIL=/usr/local/libexec/cf-jail
WORK=/tmp/cfwork

log() { [ -n "${CF_QUIET:-}" ] || printf '%s crucibleforge-entrypoint: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
# routine lines (who runs, what was seeded) only with CF_VERBOSE=1: a command's own output comes first
info() { [ -n "${CF_VERBOSE:-}" ] && log "$@"; return 0; }
say() { printf '%s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

CMD="${1:-}"
# `test` / `check` (the one-command front door and its free pre-flight) talk to people, not to logs.
{ [ "$CMD" = "test" ] || [ "$CMD" = "check" ] || [ "$CMD" = "view" ]; } && CF_QUIET=1

# the message is tailored to the mount (crucibleforge/firstrun.py folder_help): exit 24
not_writable() { exec python -m crucibleforge.firstrun folder-help "$(id -u)"; }

# ---- 1. privileges ----------------------------------------------------------
# Started as root (the default `docker run`): the benchmark runs as the OWNER OF THE DATA FOLDER, so the files
# it writes on a bind mount belong to the person who ran the command. Model-written code never runs as that user:
# it runs through the setuid helper ($JAIL) as uid 65534 with no network (crucibleforge/jail.py).
# crucibleforge/firstrun.py `prepare` picks the run user, makes sure THAT user can write the folder (chown when
# really root; it never removes a permission the run user depends on), proves it with a probe file written as that
# user, and checks that uid 65534 cannot write the folder. It prints RUID=.. RGID=.. CF_FOLDER_SHARED=.. or exits
# with a message tailored to the mount (Docker Desktop on Windows or Mac, SELinux, a network share, ...).
if [ "$(id -u)" = "0" ] && [ -z "${CF_DROPPED:-}" ]; then
    mkdir -p "$DATA_DIR" 2>/dev/null || true
    if [ "$CMD" = "test" ] || [ "$CMD" = "check" ] || [ "$CMD" = "view" ]; then
        python -m crucibleforge.firstrun has-folder || exec python -m crucibleforge.firstrun no-folder
    fi
    python -m crucibleforge.firstrun version-check "$DATA_DIR/.image-version" || exit $?
    plan="$(python -m crucibleforge.firstrun prepare)" || exit $?
    eval "$plan"
    export CF_FOLDER_SHARED
    install -d -m 0711 -o "$RUID" -g "$RGID" "$WORK" 2>/dev/null || mkdir -p "$WORK" 2>/dev/null || true
    [ -u "$JAIL" ] && export CRUCIBLEFORGE_JAIL="$JAIL"
    export CF_DROPPED=1 TMPDIR="$WORK"
    if [ "$RUID" = "0" ]; then
        # uid 0 inside a user namespace: keep only what the benchmark needs (start the jail helper, manage its
        # scratch); everything else is dropped from the bounding set.
        setpriv --inh-caps=-all --bounding-set=-all,+setuid,+setgid,+chown,+fowner,+dac_override,+kill true \
            2>/dev/null && exec setpriv --inh-caps=-all \
            --bounding-set=-all,+setuid,+setgid,+chown,+fowner,+dac_override,+kill \
            -- "$0" "$@"
        exec "$0" "$@"
    fi
    exec setpriv --reuid="$RUID" --regid="$RGID" --clear-groups --inh-caps=-all -- "$0" "$@"
fi
export TMPDIR="${TMPDIR:-$WORK}"
[ -d "$TMPDIR" ] || mkdir -p "$TMPDIR" 2>/dev/null || export TMPDIR=/tmp
[ -z "${CRUCIBLEFORGE_JAIL:-}" ] && [ -u "$JAIL" ] && export CRUCIBLEFORGE_JAIL="$JAIL"
# Model-written code runs only when the jail works here (it does not under no-new-privileges or a nosuid mount).
# Then it stands in for bubblewrap, which Docker's default profile blocks; otherwise the engine refuses as usual.
if [ -n "${CRUCIBLEFORGE_JAIL:-}" ] && (cd "$TMPDIR" && "$CRUCIBLEFORGE_JAIL" /bin/true) 2>/dev/null; then
    export CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1
else
    unset CRUCIBLEFORGE_JAIL
    [ "${CRUCIBLEFORGE_ALLOW_UNSANDBOXED:-}" = "1" ] && \
        log "WARNING: the jail helper does not work in this container (started with no-new-privileges?); model-written code runs as the benchmark user, with network"
fi

# ---- 2. writability --------------------------------------------------------
if [ ! -w "$DATA_DIR" ]; then
    info "${DATA_DIR} is not writable by UID $(id -u) (owned by $(stat -c '%u:%g' "$DATA_DIR" 2>/dev/null || echo '?'))."
    not_writable
fi
mkdir -p "$DATA_DIR/results"
[ -r "$DATA_DIR/results" ] && [ -w "$DATA_DIR/results" ] && [ -x "$DATA_DIR/results" ] \
    || die "$DATA_DIR/results is not usable by UID $(id -u) (owned by $(stat -c '%u:%g' "$DATA_DIR/results" 2>/dev/null || echo '?'))"

# ---- 3. pre-upgrade backup --------------------------------------------------
state_files() {
    [ -f "$DATA_DIR/models.yaml" ] && echo "$DATA_DIR/models.yaml"
    # `|| true`: under set -e a failing find (unreadable directory) inside the
    # $(state_files) substitution would end the container with no message.
    find "$DATA_DIR" -maxdepth 1 -name 'models.yaml.bak*' -type f 2>/dev/null || true
    find "$DATA_DIR/results" -maxdepth 1 -type f \( -name '*.csv' -o -name '*.json' -o -name '*.md' \) 2>/dev/null || true
    return 0
}

pre_upgrade_backup() {
    marker="$DATA_DIR/.image-version"
    new="${PKG_VERSION}-${IMAGE_VERSION:-dev}"
    old="$(cat "$marker" 2>/dev/null || true)"
    if [ "$old" != "$new" ]; then
        files="$(state_files)"
        if [ -n "$files" ]; then
            tag_old="$(printf '%s' "${old:-unversioned}" | tr -c 'A-Za-z0-9._+\n-' '_')"
            tag_new="$(printf '%s' "$new" | tr -c 'A-Za-z0-9._+\n-' '_')"
            dest="$DATA_DIR/backups/${tag_old}-to-${tag_new}-$(date -u '+%Y%m%dT%H%M%SZ')"
            mkdir -p "$dest"
            printf '%s\n' "$files" | while IFS= read -r f; do
                rel="${f#"$DATA_DIR"/}"
                mkdir -p "$dest/$(dirname "$rel")"
                cp -p "$f" "$dest/$rel"
            done
            info "image version changed (${old:-none} -> ${new}): state files copied to ${dest}"
            # keep the 5 newest
            ls -1dt "$DATA_DIR"/backups/*/ 2>/dev/null | tail -n +6 | while IFS= read -r d; do rm -rf "$d"; done
        fi
        printf '%s\n' "$new" > "$marker"
    fi
}
PKG_VERSION="$(python -m crucibleforge.firstrun version 2>/dev/null || echo unknown)"
# a folder written by a NEWER image is refused before anything touches it (also when started as a plain user)
[ -n "${CF_DROPPED:-}" ] || python -m crucibleforge.firstrun version-check "$DATA_DIR/.image-version" || exit $?
pre_upgrade_backup

# ---- 4. first-start seed -----------------------------------------------------
if [ ! -f "$CFG" ] && [ ! -f "$SEED_TEMPLATE" ]; then
    log "note: seed template ${SEED_TEMPLATE} not found; ${CFG} not created"
fi
if [ ! -f "$CFG" ] && [ -f "$SEED_TEMPLATE" ]; then
    mkdir -p "$(dirname "$CFG")"
    # '|' as the sed delimiter: URLs contain '/'. Both spellings of loopback:
    # the template's StudioForge entry says 127.0.0.1, the LM Studio one says
    # localhost, and inside a container either one is the container itself
    # (tests/test_deploy_seed.py applies these rules to the real template).
    # LLM_BASE_URL is escaped for the replacement side: a '&' (query string)
    # would otherwise insert the matched text, a '|' would end the expression.
    # Written to a temp file and renamed, so a failed seed never leaves a
    # partial models.yaml that blocks every later seed.
    url_sed="$(printf '%s' "$LLM_BASE_URL" | sed 's/[\\|&]/\\&/g')"
    # Generic loopback -> host rewrite FIRST, then the :1234 gateway entries ->
    # LLM_BASE_URL: the other order re-wrote an operator's own
    # LLM_BASE_URL=http://127.0.0.1:... (host networking) a second time.
    sed -e 's|http://localhost:|http://host.docker.internal:|g' \
        -e 's|http://127.0.0.1:|http://host.docker.internal:|g' \
        -e "s|http://host.docker.internal:1234/v1|${url_sed}|g" \
        "$SEED_TEMPLATE" > "$CFG.seed.$$"
    mv "$CFG.seed.$$" "$CFG"
    chmod 0600 "$CFG"
    info "seeded $CFG from ${SEED_TEMPLATE} (local provider -> ${LLM_BASE_URL})"
fi

# ---- 5/6. command + GUI token -------------------------------------------------
if [ "${1:-}" = "test" ]; then
    shift
    set -- python -m crucibleforge.quicktest "$@"
elif [ "${1:-}" = "check" ]; then
    shift
    set -- python -m crucibleforge.quicktest --check "$@"
elif [ "${1:-}" = "view" ]; then
    # the finished results, read only, in a browser: publish the port as -p 127.0.0.1:8377:8377
    shift
    set -- python -m crucibleforge.viewserver "$@"
elif [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ] || ! command -v "$1" >/dev/null 2>&1; then
    set -- crucibleforge "$@"
fi
# (CRUCIBLEFORGE_GUI_TOKEN is read by the GUI itself; it is NOT appended to argv,
# where it would show in `ps` and break `gui token show` with an unknown --token.)

info "data=${DATA_DIR} uid=$(id -u) gid=$(id -g) version=${PKG_VERSION}${IMAGE_VERSION:+ image ${IMAGE_VERSION}}"
# exec: the app becomes the process that receives SIGTERM.
exec "$@"
