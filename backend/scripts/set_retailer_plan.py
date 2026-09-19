"""Set a retailer's plan / rate-card data (Retailer.plan_data). Safe to repeat.

    cd backend
    python scripts/set_retailer_plan.py retailer1 data/fixtures/retailer1_plan_demo.json

The JSON object in the file REPLACES the retailer's current plan data (it is a flat per-retailer
lookup, not versioned). Checks whose source_of_truth is "RETAILER_PLAN.<key>" read from it.
Run from backend/ so .env is found.
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Retailer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("retailer_code")
    parser.add_argument("plan_file", type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan_file.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        print(f"{args.plan_file} must hold a JSON object", file=sys.stderr)
        return 1

    with SessionLocal() as session:
        retailer = session.scalar(select(Retailer).where(Retailer.code == args.retailer_code))
        if retailer is None:
            print(f"Unknown retailer {args.retailer_code!r}", file=sys.stderr)
            return 1
        if retailer.plan_data == plan:
            print(f"Already set: {args.retailer_code} plan_data is unchanged - nothing to do")
            return 0
        before = retailer.plan_data
        retailer.plan_data = plan
        session.commit()
        print(f"Set {args.retailer_code} plan_data: {before!r} -> {plan!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
