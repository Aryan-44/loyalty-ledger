"""Checks on a small generated dataset: reproducible, and every rule holds."""
from collections import Counter, defaultdict

import pytest

from loyalty.generator import SCALES, SIM_END, generate

SEED = 20261022
SIZE = SCALES["tiny"]


@pytest.fixture(scope="module")
def engine():
    return generate(SIZE["customers"], SIZE["orders"], SEED)


def _fingerprint(e):
    return ([(t.txn_id, t.account_id, t.txn_type, t.points, t.created_at) for t in e.ledger],
            [(o.order_id, o.account_id, o.order_at, o.total_paid) for o in e.orders.values()])


def test_same_seed_gives_identical_data(engine):
    again = generate(SIZE["customers"], SIZE["orders"], SEED)
    assert _fingerprint(again) == _fingerprint(engine)


def test_different_seed_gives_different_data(engine):
    other = generate(SIZE["customers"], SIZE["orders"], SEED + 1)
    assert _fingerprint(other) != _fingerprint(engine)


def test_exact_master_volumes(engine):
    assert len(engine.customers) == SIZE["customers"]
    assert len(engine.orders) == SIZE["orders"]


def test_balance_equals_ledger_sum_for_every_account(engine):
    sums = defaultdict(int)
    for t in engine.ledger:
        sums[t.account_id] += t.points
    for acc in engine.accounts.values():
        assert acc.points_balance == sums[acc.account_id], acc.account_id


def test_reversals_reference_the_accrual_of_the_same_order(engine):
    by_id = {t.txn_id: t for t in engine.ledger}
    reversals = [t for t in engine.ledger if t.txn_type == "REVERSAL"]
    assert reversals
    for r in reversals:
        original = by_id[r.reverses_txn_id]
        assert original.txn_type == "ACCRUAL"
        assert original.order_id == r.order_id
        assert original.created_at <= r.created_at


def test_no_lot_is_over_consumed_or_expired_twice(engine):
    lots = {t.txn_id: t for t in engine.ledger if t.txn_type in ("ACCRUAL", "BONUS")}
    used = Counter()
    expired = Counter()
    by_id = {t.txn_id: t for t in engine.ledger}
    for a in engine.lot_allocations:
        used[a.lot_txn_id] += a.points
        if by_id[a.debit_txn_id].txn_type == "EXPIRY":
            expired[a.lot_txn_id] += 1
    for lot_id, points in used.items():
        assert points <= lots[lot_id].points, lot_id
    assert expired and max(expired.values()) == 1


def test_every_debit_is_fully_traced_to_lots(engine):
    allocated = Counter()
    for a in engine.lot_allocations:
        allocated[a.debit_txn_id] += a.points
    for t in engine.ledger:
        if t.txn_type in ("REDEMPTION", "EXPIRY"):
            assert allocated[t.txn_id] == -t.points, t.txn_id


def test_final_tier_follows_configured_thresholds(engine):
    for acc in engine.accounts.values():
        spend = engine.qualifying_spend(acc.account_id, SIM_END)
        expected = [t for t in engine.tiers if t.min_qualifying_spend <= spend][-1]
        assert acc.current_tier_id == expected.tier_id, acc.account_id


def test_foreign_keys_resolve(engine):
    ledger_ids = {t.txn_id for t in engine.ledger}
    for o in engine.orders.values():
        assert o.account_id in engine.accounts
        assert o.order_at >= engine.accounts[o.account_id].enrolled_at
    for t in engine.ledger:
        assert t.account_id in engine.accounts
        assert t.order_id is None or t.order_id in engine.orders
    for r in engine.redemptions:
        assert (r.status == "APPROVED") == (r.txn_id in ledger_ids)
    for a in engine.lot_allocations:
        assert a.debit_txn_id in ledger_ids and a.lot_txn_id in ledger_ids


def test_edge_cases_are_present(engine):
    statuses = Counter(o.status for o in engine.orders.values())
    assert statuses["PARTIALLY_RETURNED"] and statuses["RETURNED"]
    assert Counter(t.txn_type for t in engine.ledger)["EXPIRY"] > 0
    assert any(c.reason == "UPGRADE" for c in engine.tier_changes)
    assert any(c.reason == "DOWNGRADE" for c in engine.tier_changes)
    same_second = Counter((r.account_id, r.requested_at) for r in engine.redemptions)
    assert max(same_second.values()) >= 2  # concurrent attempts happened
