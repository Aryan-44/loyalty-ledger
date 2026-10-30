"""Write engine tables to disk (CSV, JSON, SQLite, SQL), and read CSV back with types."""
import csv
import hashlib
import json
import platform
import sqlite3
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


def _json_value(value):
    """JSON keeps ints/bools native; money stays an exact decimal string."""
    if value is None or isinstance(value, (bool, int)):
        return value
    return _cell(value)


def write_text(path: Path, text: str) -> None:
    """UTF-8 with "\\n" line endings on every OS, so checksums match across platforms."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(rows: list, path: Path) -> None:
    data = [{f.name: _json_value(getattr(r, f.name)) for f in fields(r)} for r in rows]
    write_text(path, json.dumps(data, indent=1))


def write_json_tables(data: dict[str, list], out_dir: Path) -> None:
    for name, rows in data.items():
        write_json(rows, out_dir / f"{name}.json")


# ---------------------------------------------------------------- SQL

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _sql_value(value):
    if value is None or isinstance(value, int):  # bool is an int -> 0/1
        return int(value) if isinstance(value, bool) else value
    return _cell(value)


def write_sqlite(data: dict[str, list], db_path: Path) -> None:
    """Load every table into SQLite with constraints ON.

    Foreign keys are deferred, so the whole load is one transaction and SQLite
    checks every relationship at COMMIT - a load that breaks a key fails.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        with con:  # one transaction
            for name, rows in data.items():
                columns = [f.name for f in fields(TABLE_MODELS[name])]
                sql = (f"INSERT INTO {name} ({', '.join(columns)}) "
                       f"VALUES ({', '.join('?' * len(columns))})")
                con.executemany(sql, ([_sql_value(getattr(r, c)) for c in columns] for r in rows))
        con.execute("VACUUM")
    finally:
        con.close()


def _sql_literal(value) -> str:
    value = _sql_value(value)
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"  # money stays an exact decimal string


def write_sql_dump(data: dict[str, list], sql_path: Path) -> None:
    """Seed script: schema, then INSERTs parent tables first, in one transaction.

    Replay with `sqlite3 new.db < loyalty_seed.sql`. Built from the in-memory
    rows (not sqlite's iterdump, which orders tables alphabetically and would
    insert children before their parent tables exist).
    """
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sql_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("-- Loyalty ledger seed data. Regenerate with the command in manifest.json.\n")
        fh.write("PRAGMA foreign_keys = ON;\nBEGIN TRANSACTION;\n\n")
        fh.write(SCHEMA_SQL.read_text(encoding="utf-8"))
        for name, rows in data.items():
            columns = [f.name for f in fields(TABLE_MODELS[name])]
            prefix = f"INSERT INTO {name} ({', '.join(columns)}) VALUES ("
            fh.write(f"\n-- {name}: {len(rows):,} rows\n")
            for r in rows:
                fh.write(prefix + ", ".join(_sql_literal(getattr(r, c)) for c in columns) + ");\n")
        fh.write("\nCOMMIT;\n")


# ---------------------------------------------------------------- manifest

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out: Path, params: dict, data: dict[str, list], labels: list) -> dict:
    """Describe the run: how to regenerate it, provenance, row counts, file checksums.

    No wall-clock timestamp on purpose: the same seed must give a byte-identical
    manifest, which is what the reproducibility check compares.
    """
    files = {}
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files[path.relative_to(out).as_posix()] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "dataset": "loyalty-ledger synthetic loyalty program",
        "generator_version": "0.1.0",
        "command": params["command"],
        "seed": params["seed"],
        "parameters": {k: v for k, v in params.items() if k not in ("command", "seed")},
        "python": platform.python_version(),
        "source_data": {
            "external_data_used": False,
            "provenance": "100% synthetic. Names and cities come from small built-in lists; "
                          "emails use the reserved example.com domain (RFC 2606). "
                          "No personal, confidential or third-party data.",
        },
        "tables": {name: {"rows": len(rows), "columns": [f.name for f in fields(TABLE_MODELS[name])]}
                   for name, rows in data.items()},
        "labels": {"ground_truth_rows": len(labels)},
        "files": files,
    }
    write_text(out / "manifest.json", json.dumps(manifest, indent=2))
    return manifest
