"""The `potkeeper` command: import, categorise, report."""
import argparse
import json
import sys

from . import store
from .budget import Budget, month_status
from .charts import category_chart
from .export import to_csv, to_json
from .goals import from_json as goal_from_json, monthly_needed
from .importer import import_csv
from .money import format_cents
from .reports import month_report, year_end
from .rules import Rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="potkeeper")
    p.add_argument("--ledger", default="ledger.json")
    p.add_argument("--household", default="household.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import a CSV statement")
    imp.add_argument("csv")
    cat = sub.add_parser("categorise", help="apply the rules file to uncategorised transactions")
    cat.add_argument("rules")
    mon = sub.add_parser("month", help="month report")
    mon.add_argument("month")
    ye = sub.add_parser("year-end", help="year-end report")
    ye.add_argument("year", type=int)
    ye.add_argument("--json", action="store_true")
    bud = sub.add_parser("budget", help="budget status of a month")
    bud.add_argument("budgets", help="JSON file: a list of budgets")
    bud.add_argument("month")
    gl = sub.add_parser("goals", help="what to put aside this month")
    gl.add_argument("goals", help="JSON file: a list of goals")
    gl.add_argument("month")
    ex = sub.add_parser("export", help="export the ledger")
    ex.add_argument("--format", choices=["csv", "json"], default="csv")
    ex.add_argument("--from", dest="frm")
    ex.add_argument("--to")
    ch = sub.add_parser("chart", help="spending per category of a month")
    ch.add_argument("month")
    return p


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None, out=sys.stdout) -> int:
    a = build_parser().parse_args(argv)
    ledger = store.load_ledger(a.ledger)
    if a.cmd == "import":
        with open(a.csv, encoding="utf-8") as f:
            r = import_csv(ledger, f.read())
        store.save_ledger(ledger, a.ledger)
        print(f"added {r.added}, skipped {r.skipped}", file=out)
    elif a.cmd == "categorise":
        with open(a.rules, encoding="utf-8") as f:
            n = Rules.parse(f.read()).categorise(ledger)
        store.save_ledger(ledger, a.ledger)
        print(f"categorised {n}", file=out)
    elif a.cmd == "month":
        r = month_report(ledger, a.month)
        print(f"income {format_cents(r['income'])}  spending {format_cents(r['spending'])}", file=out)
        for cat_, spent in r["by_category"]:
            print(f"  {cat_:<20} {format_cents(spent):>12}", file=out)
    elif a.cmd == "budget":
        for d in _load_json(a.budgets):
            b = Budget(d["category"], int(d["amount"]), d["since"], bool(d.get("rollover", True)),
                       int(d.get("cap_months", 1)))
            s = month_status(ledger, b, a.month)
            print(f"{b.category:<16} available {format_cents(s['available']):>10}  spent {format_cents(s['spent']):>10}"
                  f"  left {format_cents(s['left']):>10}", file=out)
    elif a.cmd == "goals":
        for d in _load_json(a.goals):
            g = goal_from_json(d)
            print(f"{g.name:<16} {format_cents(monthly_needed(g, a.month)):>10} / month until {g.due}", file=out)
    elif a.cmd == "export":
        from .dates import parse_date
        ts = list(ledger)
        if a.frm or a.to:
            frm = parse_date(a.frm) if a.frm else min(t.date for t in ts)
            to = parse_date(a.to) if a.to else max(t.date for t in ts)
            ts = ledger.between(frm, to)
        out.write(to_csv(ts) if a.format == "csv" else to_json(ts) + "\n")
    elif a.cmd == "chart":
        out.write(category_chart(month_report(ledger, a.month)["by_category"]))
    elif a.cmd == "year-end":
        r = year_end(ledger, store.load_household(a.household), a.year)
        if a.json:
            print(json.dumps(r, indent=1), file=out)
        else:
            for m in r["members"]:
                print(f"{m['name']:<10} paid {format_cents(m['paid']):>12}  share {format_cents(m['share']):>12}"
                      f"  balance {format_cents(m['balance']):>12}", file=out)
            for frm, to, cents in r["settle"]:
                print(f"{frm} pays {to} {format_cents(cents)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
