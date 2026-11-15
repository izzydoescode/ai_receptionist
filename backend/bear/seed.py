"""Explicit, idempotent demo data loader."""

import argparse
import sys

from bear.db import check_schema, database_url, make_engine
from bear.store import Store

DEMO_REPAIR_ORDERS = [
    {
        "phone": "+13055550101",
        "plate": "MIA123",
        "vehicle": "2017 Chevy Malibu",
        "status": "waiting_on_parts",
        "note": "Front-right tire and wheel bearing. Bearing arrives tomorrow morning.",
        "eta": "Tomorrow by 3 PM",
        "balance_estimate": "$480",
        "ro_number": "1001",
        "customer_name": "Carlos Rivera",
    },
    {
        "phone": "+17865550102",
        "plate": "KRY456",
        "vehicle": "2015 Toyota Camry",
        "status": "in_progress",
        "note": "Rear bumper repair and paint. In the paint booth now.",
        "eta": "Thursday end of day",
        "balance_estimate": "$1,250",
        "ro_number": "1002",
        "customer_name": "Marie Jean-Baptiste",
    },
    {
        "phone": "+19545550103",
        "plate": "NYC789",
        "vehicle": "2012 Ford F-150",
        "status": "ready_for_pickup",
        "note": "Brakes done, pads and rotors front. Ready to go.",
        "eta": "Ready now",
        "balance_estimate": "$615",
        "ro_number": "1003",
        "customer_name": "Mike O'Brien",
    },
    {
        "phone": "+13055550104",
        "plate": "FLA321",
        "vehicle": "2019 Honda Civic",
        "status": "awaiting_approval",
        "note": "Estimate sent for A/C compressor. Need customer approval to proceed.",
        "eta": "1 day after approval",
        "balance_estimate": "$1,100",
        "ro_number": "1004",
        "customer_name": "Ana Gómez",
    },
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed demo repair orders")
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--shop", default="vice-city-auto")
    args = parser.parse_args(argv)
    engine = None
    try:
        engine = make_engine(database_url())
        check_schema(engine)
        store = Store(engine)
        for record in DEMO_REPAIR_ORDERS:
            store.upsert_repair_order(args.shop, **record)
    except Exception:
        print("Database unavailable or schema missing; run alembic upgrade head", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(f"seeded {len(DEMO_REPAIR_ORDERS)} demo repair orders for {args.shop}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
