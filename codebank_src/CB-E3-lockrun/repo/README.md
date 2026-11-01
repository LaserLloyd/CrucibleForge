# lockrun.sh

Runs a command while holding a named lock, so two copies of a nightly job
(timer + a manual run) never overlap. Bash + util-linux `flock` only.

    lockrun.sh [-w SECONDS] NAME -- COMMAND [ARG...]

* The lock file is `$LOCKDIR/NAME.lock` (`LOCKDIR` defaults to
  `/tmp/lockrun`, created if missing). NAME is one or more of
  `A-Z a-z 0-9 . _ -`.
* If another lockrun holds NAME: without `-w`, give up at once; with
  `-w SECONDS` (a whole number), wait up to that long. Giving up prints
  `lockrun: NAME is busy` on stderr and exits 75.
* COMMAND runs with its arguments exactly as given (spaces, empty strings,
  glob characters untouched) and with lockrun's stdin. While it runs,
  `$LOCKDIR/NAME.pid` holds the COMMAND's pid (one line).
* lockrun exits with COMMAND's exit status (128+N when COMMAND died of
  signal N). The pid file is removed when lockrun ends, however it ends.
* SIGTERM or SIGINT sent to lockrun is passed on to COMMAND; lockrun then
  waits for COMMAND, cleans up and exits 128+N for the signal it received.
* Usage errors (no NAME, a bad NAME, no `--`, no COMMAND, a bad `-w`)
  print a usage message on stderr and exit 64, without running anything.
