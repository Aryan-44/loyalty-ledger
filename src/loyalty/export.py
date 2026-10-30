"""Write engine tables to disk."""
import csv
import json
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


def write_rows(rows: list, path: Path, row_type=None) -> None:
    """One CSV for a list of dataclass rows (header written even when empty)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [f.name for f in fields(row_type or rows[0])]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(getattr(row, c)) for c in columns])


def write_csv(engine: LoyaltyEngine, out_dir: Path) -> dict[str, int]:
    counts = {}
    for name, rows in tables(engine).items():
        if rows:
            write_rows(rows, out_dir / f"{name}.csv")
        counts[name] = len(rows)
    return counts


def write_json(rows: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{f.name: _cell(getattr(r, f.name)) for f in fields(r)} for r in rows]
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
