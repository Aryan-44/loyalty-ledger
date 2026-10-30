"""Write engine tables to disk."""
import csv
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from .engine import LoyaltyEngine


def tables(engine: LoyaltyEngine) -> dict[str, list]:
    """Every exported table, parents before children."""
    return {
        "tiers": engine.tiers,
        "rules": engine.rules,
        "rewards": list(engine.rewards.values()),
        "customers": engine.customers,
        "loyalty_accounts": list(engine.accounts.values()),
        "orders": list(engine.orders.values()),
        "order_lines": engine.order_lines,
        "order_returns": engine.order_returns,
        "redemptions": engine.redemptions,
        "expiry_batches": engine.expiry_batches,
        "points_transactions": engine.ledger,
        "lot_allocations": engine.lot_allocations,
        "transaction_rules": engine.transaction_rules,
        "tier_changes": engine.tier_changes,
        "exceptions": engine.exceptions,
    }


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csv(engine: LoyaltyEngine, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, rows in tables(engine).items():
        if not rows:
            counts[name] = 0
            continue
        columns = [f.name for f in fields(rows[0])]
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_cell(getattr(row, c)) for c in columns])
        counts[name] = len(rows)
    return counts
