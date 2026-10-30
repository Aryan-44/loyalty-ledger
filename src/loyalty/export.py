"""Write engine tables to disk, and read them back with their types."""
import csv
import json
import types
import typing
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import models
from .engine import LoyaltyEngine

TABLE_MODELS = {
    "tiers": models.Tier,
    "rules": models.Rule,
    "rewards": models.Reward,
    "customers": models.Customer,
    "loyalty_accounts": models.LoyaltyAccount,
    "orders": models.Order,
    "order_lines": models.OrderLine,
    "order_returns": models.OrderReturn,
    "redemptions": models.Redemption,
    "expiry_batches": models.ExpiryBatch,
    "points_transactions": models.PointsTransaction,
    "lot_allocations": models.LotAllocation,
    "transaction_rules": models.TransactionRule,
    "tier_changes": models.TierChange,
    "exceptions": models.ExceptionRecord,
}


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
        write_rows(rows, out_dir / f"{name}.csv", TABLE_MODELS[name])
        counts[name] = len(rows)
    return counts


def _parser(annotation):
    """Turn a dataclass field annotation into a function str -> typed value."""
    args = typing.get_args(annotation)
    optional = isinstance(annotation, types.UnionType) and type(None) in args
    base = next(a for a in args if a is not type(None)) if optional else annotation
    convert = {int: int, Decimal: Decimal, datetime: datetime.fromisoformat,
               bool: lambda s: s == "true"}.get(base, str)
    if optional:
        return lambda s: None if s == "" else convert(s)
    return convert


def read_rows(path: Path, row_type) -> list:
    hints = typing.get_type_hints(row_type)
    parsers = {f.name: _parser(hints[f.name]) for f in fields(row_type)}
    with open(path, newline="", encoding="utf-8") as fh:
        return [row_type(**{k: parsers[k](v) for k, v in rec.items()}) for rec in csv.DictReader(fh)]


def read_tables(data_dir: Path) -> dict[str, list]:
    return {name: read_rows(data_dir / f"{name}.csv", model) for name, model in TABLE_MODELS.items()}


def write_json(rows: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{f.name: _cell(getattr(r, f.name)) for f in fields(r)} for r in rows]
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
