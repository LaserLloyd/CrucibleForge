# nightwatch

Nightly check that the home server's backups are fresh. A user timer runs
`python -m nightwatch.main config/nightwatch.toml` at 07:00; it prints a
one-line report and exits 0 (all fresh), 1 (something stale or missing) or
2 (it could not run: bad config, unreadable folder).

Config (`config/nightwatch.toml`), one `[[target]]` table per backup folder:

    [verify]
    max_age_hours = 26        # a backup older than this is STALE

    [[target]]
    name = "photos"
    dir = "/var/mnt/backup/photos"
    pattern = "*.tar.zst"

Release 2.0 renamed `[verify] max_age_h` to `max_age_hours`. Configs on the
households' boxes still say `max_age_h`, so it must keep working: the old key
is read when the new one is absent (the new key wins when both are there),
with the warning `max_age_h is deprecated; use max_age_hours` written to
stderr. With neither key the limit is 26 hours. A limit that is not a
positive number is a config error (exit 2).

Age = now minus the newest matching file's modification time, in hours.
Times are compared as real instants: the box's local time zone (Asia/Tokyo
here, Europe/Berlin at another household) must not change any result.
