"""Explicit, idempotent demo data loader."""

import argparse
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bear.db import check_schema, database_url, make_engine
from bear.shops import load_shops
from bear.store import Store

DEFAULT_SHOPS_DIR = Path(__file__).resolve().parents[2] / "shops"

DEMO_REPAIR_ORDERS = [
    {
        "phone": "+13055550101",
        "plate": "MIA123",
        "vehicle": "2017 Chevy Malibu",
        "status": "waiting_on_parts",
        "note": "Front-right tire and wheel bearing. Bearing arrives tomorrow morning.",
        "eta": "",
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
        "eta": "",
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
        "customer_name": "Daniel Brooks",
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


# RO number -> (days from the seeding day, local finish time). Others keep only their free-text note.
_DEMO_FINISH_TIMES = {"1001": (1, time(15, 0)), "1002": (3, time(17, 0))}


def demo_repair_orders(shop_tz: ZoneInfo, now: datetime | None = None) -> list[dict]:
    """The four demo ROs, with finish times relative to today in the shop's time zone."""
    today = (now or datetime.now(shop_tz)).astimezone(shop_tz).date()
    orders = []
    for template in DEMO_REPAIR_ORDERS:
        order = dict(template, eta_at=None)
        if template["ro_number"] in _DEMO_FINISH_TIMES:
            days_ahead, finish_time = _DEMO_FINISH_TIMES[template["ro_number"]]
            order["eta_at"] = datetime.combine(today + timedelta(days=days_ahead), finish_time, tzinfo=shop_tz)
        orders.append(order)
    return orders


def shop_timezone(shop_id: str, fallback_tz: str) -> ZoneInfo:
    """The shop file's time zone, or the --tz fallback for a shop id with no file."""
    shops = load_shops(os.getenv("SHOPS_DIR") or DEFAULT_SHOPS_DIR)
    return shops[shop_id].tz if shop_id in shops else ZoneInfo(fallback_tz)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed demo repair orders")
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--shop", default="vice-city-auto")
    parser.add_argument("--tz", default="America/New_York", help="time zone when the shop has no shop file")
    args = parser.parse_args(argv)
    shop_tz = shop_timezone(args.shop, args.tz)
    engine = None
    try:
        engine = make_engine(database_url())
        check_schema(engine)
        store = Store(engine)
        for record in demo_repair_orders(shop_tz):
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
