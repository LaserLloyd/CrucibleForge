# hearth: systemd user units

Units in `systemd/` are installed into `~/.config/systemd/user/` and run by
the user's systemd (`systemctl --user`). House style: see
`systemd/hearth-web.service`.

## Request: nightly backup

Add `systemd/hearth-backup.service` and `systemd/hearth-backup.timer`:

* Run `/home/alex/.local/bin/hearth-backup --quiet` once per night at
  02:30 local time. If the box was off at 02:30, run it once soon after the
  next boot instead.
* It is a run-to-completion job: no restarts. Give it at most 2 hours, then
  stop it.
* It needs the network: wait for network-online.
* Read extra settings from `%h/.config/hearth/backup.env` when that file
  exists; a missing file must not stop the job.
* Be gentle with the box: nice level 10 and the idle I/O scheduling class.
* If it fails, start `hearth-alert@%n.service` (the alert unit gets the
  failed unit's name).
* Only the timer is enabled (`systemctl --user enable --now
  hearth-backup.timer`); the service itself has no [Install] section.
