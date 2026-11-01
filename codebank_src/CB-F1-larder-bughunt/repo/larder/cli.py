"""`python -m larder.cli` (not used by the tests)."""
import sys

from .store import Pantry


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = Pantry(argv[0] if argv else "larder.db")
    for item in p.items():
        qty, unit = p.stock(item)
        print(f"{item}: {qty:g} {unit}")


if __name__ == "__main__":
    main()
