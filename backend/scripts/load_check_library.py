"""Load a check-library JSON file into the database. Safe to run repeatedly.

    cd backend
    python scripts/load_check_library.py [path/to/library.json]

Defaults to data/fixtures/retailer1_check_library_v1.json. If the retailer's
library at that version already exists nothing is written. If the existing
checks differ from the file, that's reported (exit 1): change the version
instead of editing a loaded one. Run from backend/ so .env is found.
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal  # noqa: E402
from app.models import CheckLibrary  # noqa: E402
from app.repositories.base import BaseRepository  # noqa: E402
from app.repositories.retailer_repository import RetailerRepository  # noqa: E402
from app.services.checks.library_loader import load_check_library_from_json  # noqa: E402

DEFAULT_PATH = BACKEND_DIR / "data/fixtures/retailer1_check_library_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    path = parser.parse_args().path

    data = json.loads(path.read_text(encoding="utf-8"))
    retailer_code, version = data["retailer"]["code"], data["library"]["version"]
    wanted = {c["code"] for c in data["checks"]}

    with SessionLocal() as session:
        retailer = next(iter(RetailerRepository(session).list(code=retailer_code)), None)
        existing = retailer and next(
            iter(BaseRepository(CheckLibrary, session).list(retailer_id=retailer.id, version=version)),
            None,
        )
        if existing:
            loaded = {c.code for c in existing.checks}
            print(f"Already loaded: {retailer_code} v{version} (library id={existing.id}, {len(loaded)} checks) - skipped")
            if loaded != wanted:
                print(
                    f"WARNING: checks differ from {path.name} "
                    f"(only in DB: {sorted(loaded - wanted)}, only in file: {sorted(wanted - loaded)}). "
                    "Bump the version to load changes.",
                    file=sys.stderr,
                )
                return 1
            return 0

        library = load_check_library_from_json(session, data)
        print(f"Loaded: {retailer_code} v{version} (library id={library.id}, {len(library.checks)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
