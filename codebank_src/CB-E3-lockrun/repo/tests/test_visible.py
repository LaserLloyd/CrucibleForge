import os
import subprocess


def test_runs_command(tmp_path):
    env = {**os.environ, "LOCKDIR": str(tmp_path)}
    p = subprocess.run(["bash", "lockrun.sh", "job", "--", "echo", "hi"], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout == "hi\n"
