# shoplist

The family shopping list syncs between phones that are often offline. Each
phone holds a `Replica`; phones exchange their full state and `merge` it.

## Data model (the contract)

* Item key = the name with surrounding whitespace removed, inner runs of
  whitespace collapsed to one space, then `casefold()`. "  Oat  Milk" and
  "oat milk" are the same item. The display name is the one written by the
  winning `name` write (see below).
* Every item has four last-writer-wins registers: `name`, `qty` (int >= 1,
  default 1), `checked` (bool, default False) and `present` (bool). Every
  write carries a stamp `(lamport, replica_id)`; of two writes to the same
  register, the one with the larger stamp wins (tuple comparison).
* Exception: for `present`, when two writes have the same `lamport`, the
  value True (an add) wins whatever the replica ids are.
* `add(name, qty=1)` writes `present=True`, `name` and `qty`.
  `set_qty`, `check`/`uncheck` write their register (and nothing else).
  `remove(name)` writes `present=False`, also for an item this replica has
  never seen (so an older add merged later cannot bring it back).
* Lamport clock: every local operation first increments the replica's clock
  by one and stamps all its writes with the new value. After `merge`, the
  clock is at least the largest lamport value in either state.
* `merge(other)` changes this replica to the merge of both states; merging
  is commutative, associative and idempotent: replicas that have seen the
  same writes, in any order and any number of times, have equal `state()`.
* `items()` lists the present items as `(display name, qty, checked)`,
  sorted by key.
* `dump()` / `Replica.load(text)` round-trip the full state (JSON).
