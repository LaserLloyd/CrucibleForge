# chorechart

Weekly chore rota printed on the fridge. `chorechart.config.load(path)`
reads members and chores from a small text file; `chorechart.table.render`
prints a rota; `chorechart.rotation.assign` decides who does what (NOT
IMPLEMENTED YET: see the spec below).

## assign(chores, members, week, away=frozenset()) -> dict

Returns `{chore: member or None}` for every chore, in `chores` order.

1. Chores are handled in list order. Chore i's first candidate in week w is
   `members[(i + w) % len(members)]`.
2. A candidate who is away, or who already has a chore this week, is passed
   over: try the following members in list order (wrapping around) until one
   fits.
3. If every present member already has a chore this week, the second rule's
   "already has a chore" part is dropped for the remaining chores: take the
   first present member from the candidate position on (wrapping around),
   whatever they already have.
4. If nobody is present, the chore gets None.
5. `week` may be any integer (negative too); `members` and `chores` are
   non-empty lists of distinct names, otherwise ValueError.

`schedule(chores, members, weeks, away_by_week=None)` returns
`[(week, assign(...)), ...]` for each week in `weeks`, where
`away_by_week.get(week, set())` gives that week's absentees.
