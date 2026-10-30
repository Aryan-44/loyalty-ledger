"""Validation suite: clean data passes, planted issues are EXPECTED, real bugs FAIL."""
from collections import Counter

import pytest

from loyalty.anomalies import inject
from loyalty.export import read_tables, tables, write_csv
from loyalty.generator import SCALES, generate
from loyalty.validate import run_validation

SEED = 20261022
SIZE = SCALES["tiny"]


def _fresh():
    return generate(SIZE["customers"], SIZE["orders"], SEED)


def _status(results):
    return {r.check_id: r.status for r in results}


@pytest.fixture(scope="module")
def planted():
    engine = _fresh()
    labels = inject(engine, SEED, 5)
    return engine, labels


def test_clean_data_passes_every_check():
    results = run_validation(tables(_fresh()))
    failing = [(r.check_id, r.violations, r.sample) for r in results if r.status != "PASS"]
    assert failing == []
    assert len(results) >= 70


def test_planted_issues_are_expected_never_fail(planted):
    engine, labels = planted
    status = _status(run_validation(tables(engine), labels))
    assert "FAIL" not in status.values()
    assert {k for k, v in status.items() if v == "EXPECTED"} == {
        "AR-05", "AR-06", "AR-07", "AR-08", "LC-04", "LC-06"}


def test_planted_issues_without_labels_fail(planted):
    """The same data judged without the answer key must not look clean."""
    engine, _ = planted
    status = _status(run_validation(tables(engine), labels=[]))
    assert Counter(status.values())["FAIL"] == 6


def test_unlabelled_balance_drift_fails():
    engine = _fresh()
    acc = next(iter(engine.accounts.values()))
    acc.points_balance += 1
    assert _status(run_validation(tables(engine)))["AR-05"] == "FAIL"


def test_broken_foreign_key_fails():
    engine = _fresh()
    next(iter(engine.orders.values())).account_id = "LOY-C999999"
    status = _status(run_validation(tables(engine)))
    assert status["FK-05"] == "FAIL"  # orders.account_id -> loyalty_accounts


def test_duplicate_primary_key_fails():
    engine = _fresh()
    engine.ledger[1].txn_id = engine.ledger[0].txn_id
    assert _status(run_validation(tables(engine)))["PK-11"] == "FAIL"


def test_wrong_points_calculation_fails():
    engine = _fresh()
    line = next(l for l in engine.order_lines if l.points_earned > 0)
    line.points_earned += 1
    assert _status(run_validation(tables(engine)))["AR-02"] == "FAIL"


def test_csv_round_trip_gives_same_results(planted, tmp_path):
    engine, labels = planted
    write_csv(engine, tmp_path)
    from_disk = run_validation(read_tables(tmp_path), labels)
    in_memory = run_validation(tables(engine), labels)
    assert [(r.check_id, r.status, r.violations) for r in from_disk] == \
           [(r.check_id, r.status, r.violations) for r in in_memory]
