"""Planted anomalies: labelled, isolated, kept out of the data, and detectable."""
from collections import Counter, defaultdict
from dataclasses import fields

import pytest

from loyalty.anomalies import ISSUE_TYPES, inject
from loyalty.detect import detect, score
from loyalty.export import tables
from loyalty.generator import SCALES, generate

SEED = 20261022
SIZE = SCALES["tiny"]
PER_TYPE = 5


@pytest.fixture(scope="module")
def clean():
    return generate(SIZE["customers"], SIZE["orders"], SEED)


@pytest.fixture(scope="module")
def planted():
    engine = generate(SIZE["customers"], SIZE["orders"], SEED)
    labels = inject(engine, SEED, PER_TYPE)
    return engine, labels


def test_clean_data_has_no_findings(clean):
    assert detect(tables(clean)) == []


def test_every_type_is_planted(planted):
    _, labels = planted
    assert Counter(g.issue_type for g in labels) == {t: PER_TYPE for t in ISSUE_TYPES}


def test_each_planted_account_has_exactly_one_issue(planted):
    _, labels = planted
    per_account = Counter(g.account_id for g in labels)
    assert max(per_account.values()) == 1


def test_detector_finds_all_planted_issues_and_nothing_else(planted):
    engine, labels = planted
    metrics = score(detect(tables(engine)), labels)
    assert metrics["ALL"]["recall"] == 1.0
    assert metrics["ALL"]["precision"] == 1.0


def test_unlabelled_accounts_are_untouched(planted):
    """Only BALANCE_MISMATCH accounts may disagree with their ledger."""
    engine, labels = planted
    mismatched = {g.account_id for g in labels if g.issue_type == "BALANCE_MISMATCH"}
    sums = defaultdict(int)
    for t in engine.ledger:
        sums[t.account_id] += t.points
    for acc in engine.accounts.values():
        assert (acc.points_balance != sums[acc.account_id]) == (acc.account_id in mismatched)


def test_running_balances_stay_consistent_after_injection(planted):
    engine, _ = planted
    running = defaultdict(int)
    for t in engine.ledger:
        running[t.account_id] += t.points
        assert t.balance_after == running[t.account_id], t.txn_id


def test_ledger_stays_in_time_order_with_unique_ids(planted):
    engine, _ = planted
    times = [t.created_at for t in engine.ledger]
    assert times == sorted(times)
    assert len({t.txn_id for t in engine.ledger}) == len(engine.ledger)


def test_labels_never_leak_into_data_tables(planted):
    engine, _ = planted
    for name, rows in tables(engine).items():
        if rows:
            columns = {f.name for f in fields(rows[0])}
            assert not columns & {"issue_type", "ground_truth", "is_anomaly", "label"}, name


def test_injection_is_reproducible():
    a = generate(SIZE["customers"], SIZE["orders"], SEED)
    b = generate(SIZE["customers"], SIZE["orders"], SEED)
    la, lb = inject(a, SEED, PER_TYPE), inject(b, SEED, PER_TYPE)
    assert [(g.issue_type, g.account_id, g.entity_id) for g in la] == \
           [(g.issue_type, g.account_id, g.entity_id) for g in lb]
