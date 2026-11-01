# famcal

The family calendar. Events have a start (`dtstart`, an aware datetime in
the event's zone) and optionally a recurrence rule in a subset of iCalendar
RRULE syntax. `famcal.rrule.parse_rrule` parses rules (done);
`famcal.rrule.expand` must produce the occurrences (to do, spec in
`docs/rrule.md`). `famcal.agenda` builds the week view on top of `expand`.
