"""Export formats, manifest checksums and same-seed reproducibility."""
import json
import sqlite3

import pytest

from loyalty.cli import build, main
from loyalty.export import TABLE_MODELS, tables, write_sqlite
from loyalty.generator import SCALES, generate

SEED = 20261022


def _params(seed=SEED):
    size = SCALES["tiny"]
    return {"command": f"python -m loyalty.cli generate --scale tiny --seed {seed}", "seed": seed,
            "scale": "tiny", **size, "anomalies_per_type": 5,
            "formats": ["csv", "json", "sqlite", "sql"]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("run_a")
    return out, build(out, _params(), quiet=True)


def test_same_seed_gives_byte_identical_files(built, tmp_path):
    _, first = built
    second = build(tmp_path, _params(), quiet=True)
    assert first["files"] == second["files"]
    assert len(first["files"]) == 40


def test_different_seed_changes_the_data(built, tmp_path):
    _, first = built
    other = build(tmp_path, _params(SEED + 1), quiet=True)
    assert first["files"]["data/points_transactions.csv"] != other["files"]["data/points_transactions.csv"]


def test_manifest_describes_how_to_regenerate(built):
    out, _ = built
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["seed"] == SEED
    assert "--out" not in manifest["command"]
    assert manifest["source_data"]["external_data_used"] is False
    assert manifest["tables"]["orders"]["rows"] == SCALES["tiny"]["orders"]
    assert not any(k in json.dumps(manifest) for k in ("generated_at", "timestamp"))


def test_sqlite_has_every_row_and_no_fk_violations(built):
    out, manifest = built
    con = sqlite3.connect(out / "sql" / "loyalty.db")
    for name in TABLE_MODELS:
        assert con.execute(f"select count(*) from {name}").fetchone()[0] == manifest["tables"][name]["rows"]
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sql_seed_replays_to_the_same_database(built, tmp_path):
    out, _ = built
    fresh = sqlite3.connect(tmp_path / "replay.db")
    fresh.executescript((out / "sql" / "loyalty_seed.sql").read_text(encoding="utf-8"))
    original = sqlite3.connect(out / "sql" / "loyalty.db")
    assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
    for name in TABLE_MODELS:
        q = f"select * from {name} order by 1, 2"
        assert fresh.execute(q).fetchall() == original.execute(q).fetchall(), name


def test_sqlite_rejects_a_broken_foreign_key(tmp_path):
    engine = generate(50, 200, SEED)
    engine.order_lines[0].order_id = "ORD-2026-999999"
    with pytest.raises(sqlite3.IntegrityError):
        write_sqlite(tables(engine), tmp_path / "bad.db")


def test_sqlite_rejects_a_check_constraint_violation(tmp_path):
    engine = generate(50, 200, SEED)
    engine.ledger[0].points = -engine.ledger[0].points  # an accrual can't be negative
    with pytest.raises(sqlite3.IntegrityError):
        write_sqlite(tables(engine), tmp_path / "bad.db")


def test_json_keeps_types(built):
    out, _ = built
    rows = json.loads((out / "json" / "order_lines.json").read_text())
    first = rows[0]
    assert isinstance(first["quantity"], int)
    assert isinstance(first["eligible"], bool)
    assert isinstance(first["net_amount"], str) and "." in first["net_amount"]


def test_validate_command_detects_tampered_files(built, tmp_path, capsys):
    import shutil
    out, _ = built
    copy = tmp_path / "copy"
    shutil.copytree(out, copy)
    assert main(["validate", str(copy)]) == 0
    path = copy / "data" / "customers.csv"
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    assert main(["validate", str(copy)]) == 1
    assert "CHANGED data/customers.csv" in capsys.readouterr().out
