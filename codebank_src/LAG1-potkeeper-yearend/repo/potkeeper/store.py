"""Loading and saving the ledger file and the household file (JSON)."""
import json
import os
import tempfile

from .household import Household
from .ledger import Ledger, Transaction


def load_ledger(path: str) -> Ledger:
    if not os.path.exists(path):
        return Ledger()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    tx = data.get("transactions", [])
    if isinstance(tx, dict):
        # version 1 files keyed the transactions by id
        rows = [dict(row, id=int(key)) for key, row in tx.items()]
        last = max(tx, default="0")
    else:
        rows = tx
        last = max((row["id"] for row in rows), default=0)
    next_id = data.get("next_id", int(last) + 1)
    return Ledger((Transaction.from_json(row) for row in rows), next_id)


def save_ledger(ledger: Ledger, path: str) -> None:
    """Atomic write: a crash never leaves a half-written ledger."""
    data = {"version": 2, "next_id": ledger.next_id(), "transactions": [t.to_json() for t in ledger]}
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ledger-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_household(path: str) -> Household:
    with open(path, encoding="utf-8") as f:
        return Household.from_json(json.load(f))
