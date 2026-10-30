"""One dataclass per table. Field order = column order in the exported files."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


# ---------- master data ----------

@dataclass(slots=True)
class Tier:
    tier_id: str
    name: str
    rank: int
    min_qualifying_spend: Decimal
    effective_from: datetime


@dataclass(slots=True)
class Rule:
    rule_id: str
    rule_type: str
    description: str
    value: Decimal
    effective_from: datetime
    effective_to: datetime | None = None
    tier_id: str | None = None
    category: str | None = None

    def is_effective(self, at: datetime) -> bool:
        return self.effective_from <= at and (self.effective_to is None or at < self.effective_to)


@dataclass(slots=True)
class Reward:
    reward_id: str
    name: str
    points_cost: int
    min_tier_id: str | None = None
    active: bool = True


@dataclass(slots=True)
class Customer:
    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    signup_date: datetime


@dataclass(slots=True)
class LoyaltyAccount:
    account_id: str
    customer_id: str
    enrolled_at: datetime
    status: str
    current_tier_id: str
    points_balance: int  # cached value; must always equal the ledger sum


# ---------- orders ----------

@dataclass(slots=True)
class Order:
    order_id: str
    account_id: str
    order_at: datetime
    channel: str
    status: str  # COMPLETED | PARTIALLY_RETURNED | RETURNED
    merchandise_amount: Decimal  # sum of line net amounts (all categories)
    qualifying_amount: Decimal   # eligible lines only, before returns
    tax_amount: Decimal
    shipping_amount: Decimal
    total_paid: Decimal
    tier_id_at_order: str


@dataclass(slots=True)
class OrderLine:
    line_id: str
    order_id: str
    line_no: int
    sku: str
    category: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    eligible: bool
    points_earned: int
    returned_qty: int = 0
    points_reversed: int = 0


@dataclass(slots=True)
class OrderReturn:
    return_id: str
    order_id: str
    line_id: str
    quantity: int
    refund_amount: Decimal
    returned_at: datetime
    reversal_txn_id: str | None


# ---------- ledger ----------

@dataclass(slots=True)
class PointsTransaction:
    txn_id: str
    account_id: str
    txn_type: str  # ACCRUAL | REVERSAL | REDEMPTION | EXPIRY | ADJUSTMENT
    points: int    # signed: + earns, - spends
    created_at: datetime
    balance_after: int
    order_id: str | None = None
    redemption_id: str | None = None
    expiry_batch_id: str | None = None
    reverses_txn_id: str | None = None
    expires_on: datetime | None = None  # only on ACCRUAL rows (a "lot")


@dataclass(slots=True)
class LotAllocation:
    """Which accrual lot paid for which debit (redemption, expiry, reversal)."""
    debit_txn_id: str
    lot_txn_id: str
    points: int


@dataclass(slots=True)
class TransactionRule:
    """Bridge table: rules that decided a points transaction (N:M)."""
    txn_id: str
    rule_id: str


@dataclass(slots=True)
class Redemption:
    redemption_id: str
    account_id: str
    reward_id: str
    points_cost: int
    requested_at: datetime
    request_key: str
    status: str  # APPROVED | REJECTED
    reject_reason: str | None
    txn_id: str | None


@dataclass(slots=True)
class ExpiryBatch:
    batch_id: str
    run_at: datetime
    lots_expired: int
    accounts_affected: int
    points_expired: int


@dataclass(slots=True)
class TierChange:
    tier_change_id: str
    account_id: str
    from_tier_id: str | None
    to_tier_id: str
    changed_at: datetime
    qualifying_spend_12m: Decimal
    reason: str  # ENROLLED | UPGRADE | DOWNGRADE
    trigger_ref: str | None


@dataclass(slots=True)
class ExceptionRecord:
    """Business exceptions the operations manager should see."""
    exception_id: str
    code: str
    account_id: str
    ref_id: str
    occurred_at: datetime
    detail: str
