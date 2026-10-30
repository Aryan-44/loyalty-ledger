"""The hand-worked example (customer Asha, 6 events) replayed through the engine.

Every expected number below was calculated by hand before the code was written.
See docs/worked_example.md.
"""
from datetime import datetime
from decimal import Decimal as D

import pytest

from loyalty.engine import LineItem, LoyaltyEngine
from loyalty.models import Customer


@pytest.fixture
def run():
    engine = LoyaltyEngine()
    asha = Customer("C000001", "Asha", "Rao", "asha@example.com", "Pune", datetime(2026, 1, 1))
    acc = engine.enroll(asha, datetime(2026, 1, 1)).account_id

    # 1. buy shoes + socks (tax and shipping do not earn points)
    engine.place_order(acc, datetime(2026, 1, 5, 11, 0), [
        LineItem("SKU-SHOE-01", "Footwear", 1, D("2000")),
        LineItem("SKU-SOCK-01", "Apparel", 1, D("200")),
    ], tax=D("220"), shipping=D("50"))
    # 2. buy laptop + jacket -> crosses Silver threshold
    engine.place_order(acc, datetime(2026, 2, 10, 15, 30), [
        LineItem("SKU-LAPTOP-01", "Electronics", 1, D("18000")),
        LineItem("SKU-JACKET-01", "Apparel", 1, D("3000")),
    ], tax=D("2100"))
    # 3. return the jacket
    engine.return_items("ORD-2026-000002", line_no=2, quantity=1,
                        returned_at=datetime(2026, 2, 20, 12, 0))
    # 4 + 5. two redemption clicks in the same second
    engine.redeem(acc, "RWD-001", datetime(2026, 3, 1, 10, 0, 0), request_key="click-1")
    engine.redeem(acc, "RWD-001", datetime(2026, 3, 1, 10, 0, 0), request_key="click-2")
    # 6. expiry job
    engine.run_expiry(datetime(2027, 2, 15))
    return engine, acc


def test_ledger_matches_hand_calculation(run):
    engine, _ = run
    rows = [(t.txn_id, t.txn_type, t.points, t.balance_after, t.reverses_txn_id)
            for t in engine.ledger]
    assert rows == [
        ("PTS-2026-00000001", "ACCRUAL", 220, 220, None),
        ("PTS-2026-00000002", "ACCRUAL", 2100, 2320, None),
        ("PTS-2026-00000003", "REVERSAL", -300, 2020, "PTS-2026-00000002"),
        ("PTS-2026-00000004", "REDEMPTION", -1500, 520, None),
        ("PTS-2027-00000001", "EXPIRY", -520, 0, None),
    ]


def test_balance_equals_ledger_sum(run):
    engine, acc = run
    assert engine.accounts[acc].points_balance == sum(t.points for t in engine.ledger) == 0


def test_qualifying_spend_ignores_tax_and_shipping(run):
    engine, _ = run
    first = engine.orders["ORD-2026-000001"]
    assert first.qualifying_amount == D("2200")
    assert first.total_paid == D("2470")


def test_lot_allocations_use_oldest_points_first(run):
    engine, _ = run
    allocs = [(a.debit_txn_id, a.lot_txn_id, a.points) for a in engine.lot_allocations]
    assert allocs == [
        ("PTS-2026-00000003", "PTS-2026-00000002", 300),   # reversal hits its own lot
        ("PTS-2026-00000004", "PTS-2026-00000001", 220),   # redemption: lot A first
        ("PTS-2026-00000004", "PTS-2026-00000002", 1280),  # then lot B
        ("PTS-2027-00000001", "PTS-2026-00000002", 520),   # only lot B had points left
    ]


def test_concurrent_redemption_second_is_rejected(run):
    engine, _ = run
    first, second = engine.redemptions
    assert (first.redemption_id, first.status) == ("RDM-2026-000001", "APPROVED")
    assert (second.redemption_id, second.status, second.reject_reason) == (
        "RDM-2026-000002", "REJECTED", "INSUFFICIENT_BALANCE")
    assert second.txn_id is None


def test_retried_request_key_does_not_debit_twice(run):
    engine, acc = run
    before = len(engine.ledger)
    again = engine.redeem(acc, "RWD-001", datetime(2027, 2, 15), request_key="click-1")
    assert again.redemption_id == "RDM-2026-000001"
    assert len(engine.ledger) == before


def test_tier_upgrades_on_qualifying_spend_and_survives_return(run):
    engine, acc = run
    changes = [(c.from_tier_id, c.to_tier_id, c.reason, c.qualifying_spend_12m)
               for c in engine.tier_changes]
    assert changes == [
        (None, "TIER-BRONZE", "ENROLLED", D("0")),
        ("TIER-BRONZE", "TIER-SILVER", "UPGRADE", D("23200")),
    ]
    # after the jacket return: 2200 + 18000 = 20200, still >= 20000
    assert engine.qualifying_spend(acc, datetime(2026, 2, 20, 12, 0)) == D("20200")


def test_points_earned_at_tier_held_when_buying(run):
    engine, _ = run
    # order 2 crossed the Silver threshold, but was earned at the Bronze rate
    assert engine.orders["ORD-2026-000002"].tier_id_at_order == "TIER-BRONZE"
    assert engine.ledger[1].points == 2100


def test_expiry_is_idempotent(run):
    engine, _ = run
    before = len(engine.ledger)
    batch = engine.run_expiry(datetime(2027, 2, 15))
    assert len(engine.ledger) == before
    assert batch.points_expired == 0


def test_tier_review_downgrades_when_spend_leaves_window(run):
    engine, acc = run
    engine.review_tiers(datetime(2027, 2, 15))
    assert engine.accounts[acc].current_tier_id == "TIER-BRONZE"
    assert engine.tier_changes[-1].reason == "DOWNGRADE"


def test_returning_laptop_drops_tier_back_to_bronze():
    """Variant: return the laptop instead -> qualifying spend 5,200 -> Bronze."""
    engine = LoyaltyEngine()
    acc = engine.enroll(Customer("C000002", "Ben", "K", "b@example.com", "Delhi",
                                 datetime(2026, 1, 1)), datetime(2026, 1, 1)).account_id
    engine.place_order(acc, datetime(2026, 1, 5), [LineItem("S", "Footwear", 1, D("2200"))])
    engine.place_order(acc, datetime(2026, 2, 10), [
        LineItem("L", "Electronics", 1, D("18000")), LineItem("J", "Apparel", 1, D("3000"))])
    engine.return_items("ORD-2026-000002", 1, 1, datetime(2026, 2, 20))
    assert engine.accounts[acc].current_tier_id == "TIER-BRONZE"
    assert engine.ledger[-1].points == -1800


def test_return_after_points_spent_creates_debt_and_exception():
    engine = LoyaltyEngine()
    acc = engine.enroll(Customer("C000003", "Chen", "L", "c@example.com", "Goa",
                                 datetime(2026, 1, 1)), datetime(2026, 1, 1)).account_id
    engine.place_order(acc, datetime(2026, 1, 5), [LineItem("TV", "Electronics", 1, D("5000"))])
    engine.redeem(acc, "RWD-002", datetime(2026, 1, 6))          # spends 500 of 500
    engine.return_items("ORD-2026-000001", 1, 1, datetime(2026, 1, 7))
    assert engine.accounts[acc].points_balance == -500
    assert engine.exceptions[0].code == "NEGATIVE_BALANCE_AFTER_RETURN"
    # next purchase repays the debt before creating spendable points
    engine.place_order(acc, datetime(2026, 1, 8), [LineItem("P", "Footwear", 1, D("8000"))])
    assert engine.accounts[acc].points_balance == 300
    assert engine.available_points(acc, datetime(2026, 1, 8)) == 300


def test_gift_cards_earn_nothing_and_do_not_count_to_tier():
    engine = LoyaltyEngine()
    acc = engine.enroll(Customer("C000004", "Dev", "M", "d@example.com", "Agra",
                                 datetime(2026, 1, 1)), datetime(2026, 1, 1)).account_id
    order = engine.place_order(acc, datetime(2026, 1, 5),
                               [LineItem("GC", "Gift Cards", 1, D("25000"))])
    assert order.qualifying_amount == 0
    assert engine.ledger == []
    assert engine.accounts[acc].current_tier_id == "TIER-BRONZE"


def test_events_out_of_time_order_are_refused():
    engine = LoyaltyEngine()
    engine.enroll(Customer("C000005", "E", "F", "e@example.com", "X", datetime(2026, 1, 1)),
                  datetime(2026, 3, 1))
    with pytest.raises(ValueError):
        engine.run_expiry(datetime(2026, 2, 1))
