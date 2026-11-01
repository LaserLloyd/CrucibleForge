from _lr import run


def test_simple_run_and_status(tmp_path):
    p = run(tmp_path, "job", "--", "echo", "hi")
    assert p.returncode == 0 and p.stdout == "hi\n"
