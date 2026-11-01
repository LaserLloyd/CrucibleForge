"""photosort SRC LIBRARY (not used by the tests)."""
import sys
from pathlib import Path

from .catalog import connect
from .importer import import_dir


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    src, lib = Path(argv[0]), Path(argv[1])
    conn = connect(str(lib / "catalog.db"), lib)
    r = import_dir(src, lib, conn)
    print(f"{len(r['imported'])} imported, {len(r['skipped'])} duplicates, {len(r['ignored'])} ignored")


if __name__ == "__main__":
    main()
