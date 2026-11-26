"""First-run checks for the container and the one-command `test` (4.5.1).

Two jobs, both for a person who has never used Docker before:

1. ``prepare``: the container entrypoint's folder step, run as root before privileges are dropped. It decides which
   user the benchmark runs as, makes sure THAT user can write the results folder (chown when it is really root and
   the folder is not the run user's; it never removes a permission the run user depends on), verifies it by writing
   and deleting a probe file as that user, and checks that the graded code's user (65534) cannot write the folder.
   A file system that ignores ownership (Docker Desktop's Windows or Mac sharing, SMB, NFS with root squash) is
   detected by testing the result, not by its name, and the run falls back to what works and says so.
2. The messages: every way a first run can fail has one headline (``HEADLINES``), an exit code (``CODES``) and a fix
   for THIS environment. ``docs/FIRST-RUN-DOCKER.md`` lists them; ``tests/test_firstrun_messages.py`` fails when the
   page and this file disagree.

    python -m crucibleforge.firstrun prepare            # entrypoint, as root: prints RUID=.. RGID=.. SHARED=..
    python -m crucibleforge.firstrun folder-help UID    # the not-writable message for this mount, exit 24
    python -m crucibleforge.firstrun no-folder          # the "no -v" message, exit 28
    python -m crucibleforge.firstrun version-check FILE # exit 35 when a newer image wrote this folder

Nothing here grades or scores anything."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- exit codes and headlines
CODES = {
    "NO_KEY": 20, "KEY_EMPTY": 20,
    "KEY_QUOTED": 21, "KEY_OTHER_PROVIDER": 21, "KEY_REJECTED": 21,
    "NO_CREDIT": 22,
    "UNKNOWN_MODEL": 23, "LOCAL_NO_MODEL": 23,
    "NOT_WRITABLE": 24, "CAPS_DROPPED": 24,
    "NO_TERMINAL": 25, "NOT_CONFIRMED": 25,
    "LOCAL_UNREACHABLE": 26,
    "RUN_FAILED": 27,
    "NO_FOLDER": 28,
    "NETWORK": 29, "DNS": 29, "TLS": 29,
    "RATE_LIMITED": 30,
    "BAD_CONFIG": 31,
    "DISK_FULL": 32,
    "CLOCK": 33,
    "LOCAL_CONTEXT": 34,
    "FOLDER_NEWER": 35,
    "SPEND_CAP": 36,
    "LOCAL_KEY_NEEDED": 37,
    "LOCAL_KEY_REJECTED": 38,
    "STALLED": 39,
    "STOPPED": 130,
}
#: the fixed first words of each message (the code adds the details after them)
HEADLINES = {
    "NO_KEY": "No OpenRouter key was given.",
    "KEY_EMPTY": "The OpenRouter key is empty.",
    "KEY_QUOTED": "The OpenRouter key has quote marks or spaces in it.",
    "KEY_OTHER_PROVIDER": "This is not an OpenRouter key:",
    "KEY_REJECTED": "OpenRouter rejected the key",
    "NO_CREDIT": "The OpenRouter account is out of credit",
    "UNKNOWN_MODEL": "OpenRouter has no model called",
    "LOCAL_NO_MODEL": "has no model called",
    "NOT_WRITABLE": "Cannot write to the results folder you gave with -v",
    "CAPS_DROPPED": "This container cannot switch to the data folder's owner",
    "NO_TERMINAL": "No terminal to answer \"Continue?\" on.",
    "NOT_CONFIRMED": "Not started. Nothing was spent.",
    "LOCAL_UNREACHABLE": "Nothing answered at",
    "RUN_FAILED": "The run ended with code",
    "NO_FOLDER": "No results folder was given",
    "NETWORK": "The container could not reach",
    "DNS": "The container could not look up",
    "TLS": "Something between this computer and",
    "RATE_LIMITED": "is limiting requests right now (HTTP 429).",
    "BAD_CONFIG": "models.yaml has a mistake",
    "DISK_FULL": "There is not enough free disk space",
    "CLOCK": "This computer's clock looks wrong",
    "LOCAL_CONTEXT": "context window is too small for this test",
    "FOLDER_NEWER": "This results folder was last used by a newer CrucibleForge",
    "SPEND_CAP": "The run reached its spend cap",
    "LOCAL_KEY_NEEDED": "Your model server asks for a key:",
    "LOCAL_KEY_REJECTED": "The model server rejected the key in",
    "STALLED": "The model server stopped answering",
    "STOPPED": "Stopped. Nothing is lost",
}
#: start-up notes that are not failures (the run goes on)
NOTES = {
    "SHARED": "Note: this folder's file system does not keep owners apart",
    "CHOWN_IGNORED": "note: the results folder's file system keeps its own owner",
    "AS_ROOT": "note: the results folder accepts writes only from root here",
}
JAIL_UID = 65534
DEFAULT_UID = 1000
WINDOWS_FS = {"9p", "drvfs", "v9fs"}
MAC_FS = {"virtiofs", "fakeowner", "fuse.grpcfuse", "osxfs", "grpcfuse"}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs"}


def fail_lines(*lines: str, code: str) -> None:
    for ln in lines:
        print(ln, file=sys.stderr, flush=True)
    raise SystemExit(CODES[code])


# ---------------------------------------------------------------- the mount
@dataclass
class Mount:
    point: str = ""
    fstype: str = ""
    source: str = ""
    options: str = ""

    @property
    def kind(self) -> str:
        """windows | mac | network | linux | none (no mount at all: the folder lives and dies with the container)."""
        if not self.point:
            return "none"
        if self.fstype in WINDOWS_FS or "drvfs" in self.source.lower() or re.match(r"[A-Za-z]:\\", self.source):
            return "windows"
        if self.fstype in MAC_FS or self.source.startswith("/run/host_mark") or "fakeowner" in self.options:
            return "mac"
        if self.fstype in NETWORK_FS:
            return "network"
        return "linux"


def mount_of(path: str = "/data", mountinfo: str | None = None) -> Mount:
    """The mount whose mount point IS `path` (the last one wins, as the kernel stacks them); empty when `path` is
    only a directory of the container's own file system."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return Mount()
    found = Mount()
    for line in mountinfo.splitlines():
        f = line.split()
        if len(f) < 10 or "-" not in f:
            continue
        dash = f.index("-")
        point = f[4].replace("\\040", " ")
        if point == path and dash + 2 < len(f):
            found = Mount(point, f[dash + 1], f[dash + 2], " ".join(f[dash + 3:]))
    return found


def selinux_active() -> bool:
    try:
        label = Path("/proc/self/attr/current").read_text(encoding="utf-8", errors="replace").strip("\0\n ")
    except OSError:
        return False
    return label.count(":") >= 3 and "unconfined" not in label and "kernel" != label


# ---------------------------------------------------------------- folder help (not writable)
def folder_help(mount: Mount, owner: int | None, run_uid: int, selinux: bool) -> list[str]:
    """The not-writable message, tailored to what the folder really is. Never shows a fix that cannot apply."""
    lines = [HEADLINES["NOT_WRITABLE"] + " (it is /data inside the container)."]
    kind = mount.kind
    if kind == "windows":
        lines += ["  Docker Desktop shares this Windows folder with the container, but it refused the writes.",
                  "  Use a folder inside your own user folder (not OneDrive, a system folder or a network drive):",
                  "    cd $HOME; mkdir makobench   (then run the same command again from there)",
                  "  If Docker Desktop asked whether to share the folder, answer Share."]
    elif kind == "mac":
        lines += ["  Docker Desktop shares this Mac folder with the container, but it refused the writes.",
                  "  Use a folder inside your home folder:  cd ~; mkdir makobench   (then run the same command again)",
                  "  and check that Docker Desktop > Settings > Resources > File sharing lists /Users."]
    elif kind == "network":
        lines += [f"  The folder is on a network share ({mount.fstype}) that does not let the container write as you.",
                  "  Use a folder on this computer's own disk (mkdir makobench in your home folder), or add",
                  "  --user $(id -u):$(id -g) to the command."]
    elif selinux:
        lines += ["  This Linux system uses SELinux, which blocks containers from folders it has not labelled.",
                  "  Add :Z after the folder:  -v \"$PWD/makobench:/data:Z\""]
    elif owner is not None and owner not in (0, run_uid):
        lines += ["  The folder belongs to another user on this computer.",
                  "  Make it yours:  sudo chown -R $(id -u):$(id -g) makobench   (or use a new folder: mkdir makobench2)"]
    else:
        lines += ["  Create the folder yourself first, so that it belongs to you:  mkdir makobench",
                  "  If it already exists:  sudo chown -R $(id -u):$(id -g) makobench"]
    return lines


def no_folder_lines() -> list[str]:
    return [HEADLINES["NO_FOLDER"] + ", so the results would vanish with the container.",
            "  Add -v with a folder on your computer. Make the folder first, then run:",
            "    Linux/macOS:  mkdir makobench && docker run --rm -it -v \"$PWD/makobench:/data\" ...",
            "    PowerShell:   mkdir makobench; docker run --rm -it -v \"${PWD}\\makobench:/data\" ...",
            "    cmd.exe:      mkdir makobench & docker run --rm -it -v \"%cd%\\makobench:/data\" ...",
            "  Nothing was spent."]


def shared_note(mount: Mount) -> str:
    return (NOTES["SHARED"] + f" ({mount.fstype or 'this mount'}): code the model writes could also write in it. "
            "Your key and network stay out of its reach; to keep the folder out of reach too, use a folder on "
            "a Linux disk (a Docker volume, or inside WSL on Windows).")


# ---------------------------------------------------------------- prepare (entrypoint, as root)
@dataclass
class Ops:
    """What `prepare` does to the world; the tests swap in a fake file system."""
    data: str = "/data"
    real_root: bool = False

    def stat(self) -> tuple[int, int, int]:
        st = os.stat(self.data)
        return st.st_uid, st.st_gid, st.st_mode & 0o7777

    def chown(self, uid: int, gid: int) -> None:
        os.chown(self.data, uid, gid)

    def chmod(self, mode: int) -> None:
        os.chmod(self.data, mode)

    def can_switch(self, uid: int, gid: int) -> bool:
        return _setpriv(uid, gid, ["true"]) == 0

    def can_write_as(self, uid: int, gid: int) -> bool:
        probe = os.path.join(self.data, f".cf-write-test-{os.getpid()}-{uid}")
        if uid == 0 and os.geteuid() == 0:
            try:
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("x")
                os.unlink(probe)
                return True
            except OSError:
                return False
        return _setpriv(uid, gid, ["sh", "-c", ': > "$1" && rm -f "$1"', "sh", probe]) == 0

    def fix_tree(self, uid: int, gid: int, cfg: str) -> None:
        """results/, tmp/ and models.yaml follow the folder's owner (best effort; a healthy folder is untouched)."""
        for d in (os.path.join(self.data, "results"), os.path.join(self.data, "tmp")):
            try:
                if os.path.isdir(d) and os.stat(d).st_uid != uid:
                    subprocess.run(["chown", "-R", f"{uid}:{gid}", d], check=False, capture_output=True)
            except OSError:
                pass
        try:
            if os.path.isfile(cfg):
                os.chown(cfg, uid, gid)
                os.chmod(cfg, 0o600)
        except OSError:
            pass


def _setpriv(uid: int, gid: int, argv: list[str]) -> int:
    try:
        return subprocess.run(["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--", *argv],
                              check=False, capture_output=True, timeout=30).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def is_real_root() -> bool:
    """uid 0 that is the machine's root (rootful Docker, Docker Desktop's VM), not uid 0 of a user namespace
    (rootless Docker or Podman, where uid 0 in here IS the person who ran the command). CF_ASSUME_REAL_ROOT=1 makes
    a rootless engine take the rootful path; the offline e2e uses it to cover that path on a rootless machine."""
    if os.environ.get("CF_ASSUME_REAL_ROOT") == "1":
        return True
    try:
        first = Path("/proc/self/uid_map").read_text(encoding="ascii").split("\n")[0].split()
    except OSError:
        return False
    return first[:3] == ["0", "0", "4294967295"]


@dataclass
class Prepared:
    uid: int
    gid: int
    shared: bool = False
    notes: list[str] = field(default_factory=list)


def prepare(ops: Ops, puid: str | None = None, pgid: str | None = None, cfg: str = "/data/models.yaml",
            mount: Mount | None = None) -> Prepared:
    """Decide the run user, make sure that user can write the folder, prove it, then check the graded code's user
    cannot. Raises SystemExit with the tailored message when no user that this container may become can write."""
    mount = mount if mount is not None else mount_of(ops.data)
    owner, group, mode = ops.stat()
    # 1. the run user: PUID/PGID when given; else the folder's owner; a root-owned (or unmapped, 65534) folder means
    #    uid 1000 under real root (the usual first desktop user; Docker made the folder, or Docker Desktop shows every
    #    shared folder as root's), and uid 0 under rootless engines (that uid 0 is the person who ran the command)
    if puid:
        uid, gid = int(puid), int(pgid or puid)
    elif owner not in (0, JAIL_UID):
        uid, gid = owner, group
    elif ops.real_root:
        uid, gid = DEFAULT_UID, DEFAULT_UID
    else:
        uid, gid = 0, 0
    res = Prepared(uid, gid)
    # 2. may this container become that user at all (--cap-drop ALL takes setuid away)?
    if uid != 0 and not ops.can_switch(uid, gid):
        if ops.can_write_as(0, 0):
            res.uid, res.gid = 0, 0
            res.notes.append(f"note: cannot switch to uid {uid} here (capabilities dropped); files are written as uid 0")
            return _isolation(ops, res)
        fail_lines("", HEADLINES["CAPS_DROPPED"] + f" (uid {uid}): its capabilities were dropped.",
                   f"  Add --user {uid}:{gid} to the command (or leave out --cap-drop).", code="CAPS_DROPPED")
    # 3. make the folder the run user's; keep only the owner's permissions when the owner really is the run user
    if uid != 0 and owner != uid:
        try:
            ops.chown(uid, gid)
        except OSError:
            pass
    owner2, _g, mode2 = ops.stat()
    if owner2 == uid:
        try:
            ops.chmod((mode2 | 0o700) & ~0o007)      # the run user keeps rwx; the graded code's user loses all
        except OSError:
            pass
    else:
        # the file system keeps its own owner (Docker Desktop sharing, SMB, NFS root squash): never take away the
        # "other" bits the run user may be writing through
        res.notes.append(NOTES["CHOWN_IGNORED"] + f" ({mount.fstype or 'unknown'}); its permissions were left as "
                         "they are")
    # 4. prove it: write and delete a probe file as the run user
    if not ops.can_write_as(uid, gid):
        if uid != 0 and ops.real_root and ops.can_write_as(0, 0):
            res.uid, res.gid = 0, 0
            res.notes.append(NOTES["AS_ROOT"] + f" ({mount.fstype or 'unknown'}); the benchmark runs as root, "
                             "and its files belong to root")
        else:
            fail_lines("", *folder_help(mount, owner2, uid, selinux_active()), code="NOT_WRITABLE")
    if res.uid == uid:
        ops.fix_tree(res.uid, res.gid, cfg)
    return _isolation(ops, res)


def _isolation(ops: Ops, res: Prepared) -> Prepared:
    """The graded code runs as 65534: it must not be able to write the folder. Some shared file systems cannot keep
    that promise; then it is said plainly, at start-up and on the page."""
    if res.uid != JAIL_UID and ops.can_write_as(JAIL_UID, JAIL_UID):
        res.shared = True
    return res


# ---------------------------------------------------------------- version check
def version_tuple(v: str) -> tuple:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v.strip())
    return tuple(int(x) for x in m.groups()) if m else ()


def version_check(marker: str, current: str) -> list[str]:
    """A folder last used by a newer image may hold rows this image cannot read correctly: refuse, name both."""
    try:
        old = Path(marker).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    o, c = version_tuple(old.split("-")[0]), version_tuple(current)
    if o and c and o > c:
        return [HEADLINES["FOLDER_NEWER"] + f" ({old.split('-')[0]}); this image is {current}.",
                "  Use the newer image (docker pull ghcr.io/laserlloyd/crucibleforge:latest), or a new folder for this one.",
                "  Nothing was changed in the folder."]
    return []


def package_version() -> str:
    from importlib import metadata
    try:
        return metadata.version("crucibleforge-bench")
    except metadata.PackageNotFoundError:
        from .version import SUITE_VERSION
        return SUITE_VERSION


# ---------------------------------------------------------------- command line
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd == "prepare":
        ops = Ops(data=os.environ.get("CF_DATA_DIR", "/data"), real_root=is_real_root())
        res = prepare(ops, os.environ.get("PUID") or None, os.environ.get("PGID") or None,
                      cfg=os.environ.get("CRUCIBLEFORGE_CONFIG", "/data/models.yaml"))
        for n in res.notes:                 # one line each, also before `test`: the person should know
            print(n, file=sys.stderr)
        if res.shared and not os.environ.get("CF_QUIET"):   # `test`/`check` print it in their own words
            print(shared_note(mount_of(ops.data)), file=sys.stderr)
        print(f"RUID={res.uid} RGID={res.gid} CF_FOLDER_SHARED={1 if res.shared else 0}")
        return 0
    if cmd == "folder-help":
        uid = int(argv[1]) if len(argv) > 1 else os.getuid()
        m = mount_of(os.environ.get("CF_DATA_DIR", "/data"))
        try:
            owner = os.stat(m.point or "/data").st_uid
        except OSError:
            owner = None
        print("", *folder_help(m, owner, uid, selinux_active()), sep="\n", file=sys.stderr)
        return CODES["NOT_WRITABLE"]
    if cmd == "no-folder":
        print("", *no_folder_lines(), sep="\n", file=sys.stderr)
        return CODES["NO_FOLDER"]
    if cmd == "has-folder":
        return 0 if mount_of(os.environ.get("CF_DATA_DIR", "/data")).point else 1
    if cmd == "version-check":
        lines = version_check(argv[1], package_version()) if len(argv) > 1 else []
        if lines:
            print("", *lines, sep="\n", file=sys.stderr)
            return CODES["FOLDER_NEWER"]
        return 0
    if cmd == "version":
        print(package_version())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
