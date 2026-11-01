from _lr import run


def test_arguments_passed_exactly(tmp_path):
    p = run(tmp_path, "job", "--", "printf", "[%s]", "a b", "", "*", "c  d")
    assert p.returncode == 0 and p.stdout == "[a b][][*][c  d]", p


def test_stdin_and_exit_status(tmp_path):
    p = run(tmp_path, "job", "--", "bash", "-c", "read x; echo got=$x; exit 7", input="hello\n")
    assert p.returncode == 7 and p.stdout == "got=hello\n", p
    assert run(tmp_path, "job", "--", "false").returncode == 1
    p = run(tmp_path, "job", "--", "bash", "-c", "kill -9 $$")
    assert p.returncode == 137, p
