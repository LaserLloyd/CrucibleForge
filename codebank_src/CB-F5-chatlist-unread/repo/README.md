# chatlist

The thread list of the family chat's web app (vanilla JS ES modules, no
build step). The server pushes events; `events.mjs` folds them into the
client state, `unread.mjs` turns the state into the unread badges.

Events (each has `thread`):

* `{type: "message", thread, id, from}`: a new message. Ids are positive
  integers that grow within a thread.
* `{type: "edit", thread, id}`: message `id` was edited (it is still one message).
* `{type: "delete", thread, id}`: message `id` was deleted.
* `{type: "read", thread, upTo}`: the user read the thread up to message
  `upTo`, on this device or another one. Read events can arrive late and out
  of order; reading never makes a thread "less read".

Events may arrive more than once (reconnects replay recent events).

Unread rules: a thread's unread count is the number of its messages that
exist (not deleted), were not sent by the user (`state.me`), and have an id
above the thread's read mark. `totalUnread` sums all threads.
`badgeText(n)`: "" for 0, the number for 1..99, "99+" above 99.
