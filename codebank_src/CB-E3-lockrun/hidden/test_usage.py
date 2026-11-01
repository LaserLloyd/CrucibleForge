from _lr import run


def test_usage_errors(tmp_path):
    marker = tmp_path / "ran"
    for args in ((), ("job",), ("job", "echo", "x"), ("job", "--"), ("bad/name", "--", "true"),
                 ("-w", "x", "job", "--", "true"), ("-w",), ("", "--", "true"), ("a b", "--", "true")):
        p = run(tmp_path, *args, "touch", str(marker)) if args and args[-1] == "--" and len(args) > 1 and False else run(tmp_path, *args)
        assert p.returncode == 64 and p.stderr.strip() and p.stdout == "", (args, p)
    assert not marker.exists()


def test_lockdir_created(tmp_path):
    from _lr import SCRIPT
    import subprocess
    d = tmp_path / "deep" / "locks"
    p = subprocess.run(["bash", SCRIPT, "j", "--", "true"], env={"PATH": "/usr/bin:/bin", "LOCKDIR": str(d)},
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0 and (d / "j.lock").exists()
