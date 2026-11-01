"""Entry point: python -m nightwatch.main CONFIG"""
import sys

from .check import check_target
from .config import ConfigError, load


def run(config_path, now=None, out=sys.stdout, err=sys.stderr):
    try:
        cfg = load(config_path)
        results = [check_target(t, cfg["max_age_hours"], now) for t in cfg["targets"]]
    except (ConfigError, OSError) as e:
        print(f"nightwatch: {e}", file=err)
        return 2
    parts = []
    for r in results:
        age = "-" if r["age_hours"] is None else f"{r['age_hours']:.1f}h"
        parts.append(f"{r['name']}={r['status']}({age})")
    print("nightwatch: " + " ".join(parts), file=out)
    return 0 if all(r["status"] == "FRESH" for r in results) else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
