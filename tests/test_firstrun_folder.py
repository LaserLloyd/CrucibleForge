"""The results folder step of the container entrypoint (crucibleforge/firstrun.py `prepare`).

The 4.5.1 Windows blocker: Docker Desktop shows a fresh Windows folder as 0:0 777. The old entrypoint picked uid 1000
because 1000 could write, did not chown, then took the "other" bits away (0:0 770): uid 1000 could no longer write
and the first run always failed with rc 24. These tests run `prepare` on a fake file system for each kind of mount
and require the one thing that matters: afterwards the chosen user CAN write, proven the way the entrypoint proves
it, and the graded code's user (65534) cannot, or that is reported."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from crucibleforge import firstrun as fr


@dataclass
class FakeFS(fr.Ops):
    owner: int = 0
    group: int = 0
    mode: int = 0o777
    chown_works: bool = True      # False: drvfs without metadata, SMB, NFS root squash
    chmod_works: bool = True
    root_writes: bool = True      # False: NFS root squash (root is "nobody" there)
    switch: bool = True           # False: --cap-drop ALL
    calls: list = field(default_factory=list)

    def stat(self):
        return self.owner, self.group, self.mode

    def chown(self, uid, gid):
        self.calls.append(("chown", uid, gid))
        if not self.chown_works:
            raise PermissionError(1, "Operation not permitted")
        self.owner, self.group = uid, gid

    def chmod(self, mode):
        self.calls.append(("chmod", oct(mode)))
        if self.chmod_works:
            self.mode = mode

    def can_switch(self, uid, gid):
        return self.switch

    def can_write_as(self, uid, gid):
        if uid == 0:
            return self.root_writes
        if uid == self.owner:
            return bool(self.mode & 0o200)
        if gid == self.group:
            return bool(self.mode & 0o020)
        return bool(self.mode & 0o002)

    def fix_tree(self, uid, gid, cfg):
        self.calls.append(("fix_tree", uid, gid))


WIN = fr.Mount("/data", "9p", "drvfs", "rw,metadata")


def _ok(fs: FakeFS, res: fr.Prepared):
    assert fs.can_write_as(res.uid, res.gid), f"run user {res.uid} cannot write: {fs}"


def test_windows_fresh_folder_0_0_777_is_written_on_the_first_run():
    """The blocker: a fresh Docker Desktop folder (0:0 777, chown stored as metadata) works the FIRST time."""
    fs = FakeFS(real_root=True, owner=0, group=0, mode=0o777)
    res = fr.prepare(fs, mount=WIN)
    assert (res.uid, res.gid) == (1000, 1000)
    _ok(fs, res)
    assert fs.calls[0] == ("chown", 1000, 1000), "the run user must own the folder BEFORE any mode is narrowed"
    assert fs.mode == 0o770 and not res.shared


def test_windows_folder_left_at_0_0_770_by_the_old_image_is_repaired():
    """A folder the 4.5.1-dev image broke (0:0 770, maybe not empty) works again; emptiness no longer matters."""
    fs = FakeFS(real_root=True, owner=0, group=0, mode=0o770)
    res = fr.prepare(fs, mount=WIN)
    _ok(fs, res)
    assert res.uid == 1000


def test_a_file_system_that_ignores_chown_keeps_its_bits_and_says_so():
    """drvfs without metadata, SMB: owner stays 0, the mode stays 777. Never narrow the bits the user writes through;
    report that the graded code's user could write too."""
    fs = FakeFS(real_root=True, owner=0, mode=0o777, chown_works=False, chmod_works=False)
    res = fr.prepare(fs, mount=WIN)
    _ok(fs, res)
    assert res.shared
    assert not [c for c in fs.calls if c[0] == "chmod"]
    assert any(n.startswith(fr.NOTES["CHOWN_IGNORED"]) for n in res.notes)


def test_chown_ignored_but_chmod_honoured_never_locks_the_run_user_out():
    fs = FakeFS(real_root=True, owner=0, mode=0o777, chown_works=False, chmod_works=True)
    res = fr.prepare(fs, mount=WIN)
    _ok(fs, res)
    assert fs.mode == 0o777


def test_rootless_engine_runs_as_the_person_and_hides_the_folder_from_others():
    """Rootless Docker/Podman: uid 0 in here is the person who ran the command; no subuid owns their files."""
    fs = FakeFS(real_root=False, owner=0, mode=0o777)
    res = fr.prepare(fs, mount=fr.Mount("/data", "ext4", "/dev/x", "rw"))
    assert res.uid == 0
    _ok(fs, res)
    assert fs.mode == 0o770 and not res.shared
    assert not [c for c in fs.calls if c[0] == "chown"]


def test_a_folder_the_user_made_is_used_as_it_is():
    fs = FakeFS(real_root=True, owner=1000, group=1000, mode=0o755)
    res = fr.prepare(fs, mount=fr.Mount("/data", "ext4", "/dev/x", "rw"))
    assert res.uid == 1000
    _ok(fs, res)
    assert fs.mode == 0o750 and not [c for c in fs.calls if c[0] == "chown"]


def test_a_root_owned_folder_on_linux_goes_to_the_run_user():
    fs = FakeFS(real_root=True, owner=0, mode=0o755)
    res = fr.prepare(fs, mount=fr.Mount("/data", "xfs", "/dev/x", "rw"))
    _ok(fs, res)
    assert (fs.owner, res.uid) == (1000, 1000)


def test_nfs_root_squash_with_nobody_able_to_write_names_the_mount(capsys):
    fs = FakeFS(real_root=True, owner=0, mode=0o755, chown_works=False, root_writes=False)
    with pytest.raises(SystemExit) as e:
        fr.prepare(fs, mount=fr.Mount("/data", "nfs4", "srv:/x", "rw"))
    assert e.value.code == fr.CODES["NOT_WRITABLE"]
    err = capsys.readouterr().err
    assert "network share" in err and ":Z" not in err


def test_chown_refused_but_root_can_write_falls_back_to_root_with_one_line():
    fs = FakeFS(real_root=True, owner=0, mode=0o755, chown_works=False, root_writes=True)
    res = fr.prepare(fs, mount=fr.Mount("/data", "cifs", "//srv/x", "rw"))
    assert res.uid == 0
    assert any(n.startswith(fr.NOTES["AS_ROOT"]) for n in res.notes)


def test_capabilities_dropped_runs_as_uid_0_when_it_can_write():
    fs = FakeFS(real_root=True, owner=1000, group=1000, mode=0o777, switch=False)
    res = fr.prepare(fs, mount=fr.Mount("/data", "ext4", "/dev/x", "rw"))
    assert res.uid == 0


def test_puid_is_honoured():
    fs = FakeFS(real_root=True, owner=0, mode=0o755)
    res = fr.prepare(fs, puid="1234", pgid="1234", mount=fr.Mount("/data", "ext4", "/dev/x", "rw"))
    assert res.uid == 1234 and fs.owner == 1234
    _ok(fs, res)


def test_a_folder_owned_by_the_overflow_uid_is_never_run_as_the_jail_user():
    """65534 is the graded code's user: a folder that shows as 65534 (unmapped owner) must not make it the run user."""
    fs = FakeFS(real_root=True, owner=65534, group=65534, mode=0o777)
    res = fr.prepare(fs, mount=fr.Mount("/data", "ext4", "/dev/x", "rw"))
    assert res.uid != fr.JAIL_UID
    _ok(fs, res)


# ---------------------------------------------------------------- the mount and its message
MOUNTINFO = """\
22 1 0:20 / / rw,relatime - overlay overlay rw
40 22 0:51 / /data rw,noatime - 9p drvfs rw,dirsync,aname=drvfs;path=C:\\Users\\x\\makobench;metadata
"""


def test_mountinfo_finds_the_data_mount_and_its_kind():
    m = fr.mount_of("/data", MOUNTINFO)
    assert (m.fstype, m.kind) == ("9p", "windows")
    assert fr.mount_of("/data", "22 1 0:20 / / rw - overlay overlay rw\n").kind == "none"
    mac = "41 22 0:60 / /data rw - fakeowner /run/host_mark/Users rw\n"
    assert fr.mount_of("/data", mac).kind == "mac"


@pytest.mark.parametrize("kind,mount", [
    ("windows", fr.Mount("/data", "9p", "drvfs", "rw")),
    ("mac", fr.Mount("/data", "virtiofs", "x", "rw")),
    ("network", fr.Mount("/data", "nfs4", "s:/x", "rw")),
])
def test_no_selinux_advice_outside_linux_even_with_a_label(kind, mount):
    text = "\n".join(fr.folder_help(mount, 0, 1000, selinux=True))
    assert ":Z" not in text and "SELinux" not in text
    assert text.startswith(fr.HEADLINES["NOT_WRITABLE"])


def test_selinux_advice_on_linux_only_when_selinux_is_active():
    lin = fr.Mount("/data", "xfs", "/dev/x", "rw")
    assert ":Z" in "\n".join(fr.folder_help(lin, 1000, 1000, selinux=True))
    assert ":Z" not in "\n".join(fr.folder_help(lin, 1000, 1000, selinux=False))


def test_no_folder_message_shows_every_shell_and_spends_nothing():
    text = "\n".join(fr.no_folder_lines())
    assert "PowerShell" in text and "${PWD}\\makobench" in text and "$PWD/makobench" in text and "%cd%" in text
    assert "Nothing was spent" in text


def test_a_folder_from_a_newer_image_is_refused(tmp_path):
    marker = tmp_path / ".image-version"
    marker.write_text("4.6.0-v4.6.0\n", encoding="utf-8")
    assert fr.version_check(str(marker), "4.5.1")[0].startswith(fr.HEADLINES["FOLDER_NEWER"])
    marker.write_text("4.5.0-dev\n", encoding="utf-8")
    assert fr.version_check(str(marker), "4.5.1") == []
