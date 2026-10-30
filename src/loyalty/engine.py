"""The rules engine.

One public method per business event (enroll, place_order, return_items,
redeem, run_expiry, review_tiers). Each one applies the configured rules and
APPENDS rows to the ledger - nothing in the ledger is ever edited or deleted.

Key ideas:
  * Every ACCRUAL is a "lot" with its own expiry date.
  * Debits (redemption, expiry, reversal) consume lots oldest-first, and each
    consumption is written to lot_allocations so every point is traceable.
  * Balance = sum of ledger rows. The cached account.points_balance is updated
    in the same step as the ledger row so they cannot drift apart.
  * Events must be fed in time order (enforced by _advance_clock).
"""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from . import config
from .ids import IdFactory, account_id as make_account_id
from .models import (
    Customer, ExceptionRecord, ExpiryBatch, LotAllocation, LoyaltyAccount, Order,
    OrderLine, OrderReturn, PointsTransaction, Redemption, TierChange, TransactionRule,
)

CENT = Decimal("0.01")


@dataclass(slots=True)
class LineItem:
    """Input for one order line (what the customer put in the basket)."""
    sku: str
    category: str
    quantity: int
    unit_price: Decimal
    discount: Decimal = Decimal("0")


class LoyaltyEngine:
    def __init__(self, tiers=None, rules=None, rewards=None, ids=None):
        self.tiers = sorted(tiers or config.TIERS, key=lambda t: t.rank)
        self.tier_by_id = {t.tier_id: t for t in self.tiers}
        self.rules = rules or config.RULES
        self._rules_by_type = defaultdict(list)
        for rule in self.rules:
            self._rules_by_type[rule.rule_type].append(rule)
        self.rewards = {r.reward_id: r for r in (rewards or config.REWARDS)}
        self.ids = ids or IdFactory()

        # output tables
        self.customers: list[Customer] = []
        self.accounts: dict[str, LoyaltyAccount] = {}
        self.orders: dict[str, Order] = {}
        self.order_lines: list[OrderLine] = []
        self.order_returns: list[OrderReturn] = []
        self.ledger: list[PointsTransaction] = []
        self.lot_allocations: list[LotAllocation] = []
        self.transaction_rules: list[TransactionRule] = []
        self.redemptions: list[Redemption] = []
        self.expiry_batches: list[ExpiryBatch] = []
        self.tier_changes: list[TierChange] = []
        self.exceptions: list[ExceptionRecord] = []

        # working state (not exported)
        self._clock: datetime | None = None
        self._lines_by_order = defaultdict(list)
        self._accrual_by_order: dict[str, PointsTransaction] = {}
        self._lot_remaining: dict[str, int] = {}
        self._lots_by_account = defaultdict(list)   # FIFO per account
        self._open_lots = deque()                   # all lots, oldest first (for expiry)
        self._debts = defaultdict(list)             # account -> [[reversal_txn_id, points owed]]
        self._orders_by_account = defaultdict(list)
        self._net_qualifying: dict[str, Decimal] = {}  # order_id -> qualifying after returns
        self._redemption_by_key: dict[str, Redemption] = {}

    # ------------------------------------------------------------------ events

    def enroll(self, customer: Customer, enrolled_at: datetime) -> LoyaltyAccount:
        self._advance_clock(enrolled_at)
        base = self.tiers[0]
        acc = LoyaltyAccount(make_account_id(customer.customer_id), customer.customer_id,
                             enrolled_at, "ACTIVE", base.tier_id, 0)
        self.customers.append(customer)
        self.accounts[acc.account_id] = acc
        self._record_tier_change(acc, None, base.tier_id, enrolled_at, Decimal("0"),
                                 "ENROLLED", None)
        return acc

    def place_order(self, account_id: str, order_at: datetime, items: list[LineItem],
                    tax: Decimal = Decimal("0"), shipping: Decimal = Decimal("0"),
                    channel: str = "ONLINE") -> Order:
        self._advance_clock(order_at)
        acc = self.accounts[account_id]
        tier_id = acc.current_tier_id  # points are earned at the tier held when buying
        earn_rule = self._earn_rule(tier_id, order_at)
        applied_rules = [earn_rule.rule_id]
        order_id = self.ids.next("ORD", order_at.year)

        merchandise = qualifying = Decimal("0")
        total_points = 0
        for line_no, item in enumerate(items, start=1):
            net = (item.unit_price * item.quantity - item.discount).quantize(CENT)
            merchandise += net
            exclusion = self._category_rule("CATEGORY_EXCLUSION", item.category, order_at)
            if exclusion:
                eligible, points = False, 0
                applied_rules.append(exclusion.rule_id)
            else:
                multiplier_rule = self._category_rule("CATEGORY_MULTIPLIER", item.category, order_at)
                multiplier = Decimal("1")
                if multiplier_rule:
                    multiplier = multiplier_rule.value
                    applied_rules.append(multiplier_rule.rule_id)
                eligible = True
                points = _floor(net * earn_rule.value * multiplier / 100)
                qualifying += net
            total_points += points
            line = OrderLine(f"{order_id}-{line_no:02d}", order_id, line_no, item.sku,
                             item.category, item.quantity, item.unit_price, item.discount,
                             net, eligible, points)
            self.order_lines.append(line)
            self._lines_by_order[order_id].append(line)

        order = Order(order_id, account_id, order_at, channel, "COMPLETED", merchandise,
                      qualifying, tax, shipping, merchandise + tax + shipping, tier_id)
        self.orders[order_id] = order
        self._orders_by_account[account_id].append((order_at, order_id))
        self._net_qualifying[order_id] = qualifying

        if total_points > 0:
            expiry_rule = self._rule("POINTS_EXPIRY", order_at)
            txn = self._post(acc, "ACCRUAL", total_points, order_at, order_id=order_id,
                             expires_on=order_at + timedelta(days=int(expiry_rule.value)))
            self._accrual_by_order[order_id] = txn
            self._add_lot(txn)
            for rule_id in dict.fromkeys(applied_rules + [expiry_rule.rule_id]):
                self.transaction_rules.append(TransactionRule(txn.txn_id, rule_id))

        self._review_tier(acc, order_at, trigger_ref=order_id)
        return order

    def award_bonus(self, account_id: str, at: datetime, rule_type: str) -> PointsTransaction:
        """Non-purchase points (welcome, birthday). Creates a lot like any accrual."""
        self._advance_clock(at)
        acc = self.accounts[account_id]
        rule = self._rule(rule_type, at)
        expiry_rule = self._rule("POINTS_EXPIRY", at)
        txn = self._post(acc, "BONUS", int(rule.value), at,
                         expires_on=at + timedelta(days=int(expiry_rule.value)))
        self._add_lot(txn)
        self.transaction_rules.append(TransactionRule(txn.txn_id, rule.rule_id))
        self.transaction_rules.append(TransactionRule(txn.txn_id, expiry_rule.rule_id))
        return txn

    def return_items(self, order_id: str, line_no: int, quantity: int,
                     returned_at: datetime) -> OrderReturn:
        self._advance_clock(returned_at)
        order = self.orders[order_id]
        acc = self.accounts[order.account_id]
        line = self._lines_by_order[order_id][line_no - 1]
        if quantity <= 0 or line.returned_qty + quantity > line.quantity:
            raise ValueError(f"cannot return {quantity} of {line.line_id}: "
                             f"{line.quantity - line.returned_qty} left")

        # Points to take back: proportional to quantity, computed on the running
        # total so several partial returns add up to exactly points_earned.
        line.returned_qty += quantity
        target = line.points_earned * line.returned_qty // line.quantity
        points = target - line.points_reversed
        line.points_reversed = target

        refund = (line.net_amount * quantity / line.quantity).quantize(CENT)
        if line.eligible:
            self._net_qualifying[order_id] -= refund

        return_id = self.ids.next("RET", returned_at.year)
        reversal_id = None
        if points > 0:
            accrual = self._accrual_by_order[order_id]
            txn = self._post(acc, "REVERSAL", -points, returned_at, order_id=order_id,
                             reverses_txn_id=accrual.txn_id)
            reversal_id = txn.txn_id
            # take the points back from the original lot first, then oldest others
            uncovered = self._consume(acc.account_id, txn.txn_id, points, returned_at,
                                      first_lot=accrual)
            if uncovered:
                # customer already spent these points: record the debt, settle it
                # from future accruals, and flag it for operations
                self._debts[acc.account_id].append([txn.txn_id, uncovered])
                self._raise_exception("NEGATIVE_BALANCE_AFTER_RETURN", acc.account_id,
                                      return_id, returned_at,
                                      f"{uncovered} reversed points were already spent; "
                                      f"balance now {acc.points_balance}")

        self.order_returns.append(OrderReturn(return_id, order_id, line.line_id, quantity,
                                              refund, returned_at, reversal_id))
        lines = self._lines_by_order[order_id]
        order.status = ("RETURNED" if all(l.returned_qty == l.quantity for l in lines)
                        else "PARTIALLY_RETURNED")
        self._review_tier(acc, returned_at, trigger_ref=return_id)
        return self.order_returns[-1]

    def redeem(self, account_id: str, reward_id: str, requested_at: datetime,
               request_key: str | None = None) -> Redemption:
        self._advance_clock(requested_at)
        # A retried request with the same key returns the original result, so a
        # network retry can never debit twice.
        if request_key and request_key in self._redemption_by_key:
            return self._redemption_by_key[request_key]

        acc = self.accounts[account_id]
        reward = self.rewards[reward_id]
        available = self.available_points(account_id, requested_at)

        reason = None
        if not reward.active:
            reason = "REWARD_INACTIVE"
        elif reward.min_tier_id and (self.tier_by_id[acc.current_tier_id].rank
                                     < self.tier_by_id[reward.min_tier_id].rank):
            reason = "TIER_NOT_ELIGIBLE"
        elif available < reward.points_cost:
            reason = "INSUFFICIENT_BALANCE"

        redemption_id = self.ids.next("RDM", requested_at.year)
        txn_id = None
        if reason is None:
            txn = self._post(acc, "REDEMPTION", -reward.points_cost, requested_at,
                             redemption_id=redemption_id)
            self._consume(account_id, txn.txn_id, reward.points_cost, requested_at)
            txn_id = txn.txn_id

        redemption = Redemption(redemption_id, account_id, reward_id, reward.points_cost,
                                requested_at, request_key or redemption_id,
                                "REJECTED" if reason else "APPROVED", reason, txn_id)
        self.redemptions.append(redemption)
        self._redemption_by_key[redemption.request_key] = redemption
        return redemption

    def run_expiry(self, as_of: datetime) -> ExpiryBatch:
        """Expire every lot whose expiry date has passed. Safe to run twice."""
        self._advance_clock(as_of)
        batch_id = self.ids.next("EXP", as_of.year)
        expiry_rule = self._rule("POINTS_EXPIRY", as_of)
        lots = points_total = 0
        accounts = set()
        while self._open_lots and self._open_lots[0].expires_on <= as_of:
            lot = self._open_lots.popleft()
            left = self._lot_remaining[lot.txn_id]
            if left <= 0:
                continue  # fully used already - nothing to expire
            acc = self.accounts[lot.account_id]
            txn = self._post(acc, "EXPIRY", -left, as_of, expiry_batch_id=batch_id)
            self._lot_remaining[lot.txn_id] = 0
            self.lot_allocations.append(LotAllocation(txn.txn_id, lot.txn_id, left))
            self.transaction_rules.append(TransactionRule(txn.txn_id, expiry_rule.rule_id))
            lots += 1
            points_total += left
            accounts.add(acc.account_id)
        batch = ExpiryBatch(batch_id, as_of, lots, len(accounts), points_total)
        self.expiry_batches.append(batch)
        return batch

    def review_tiers(self, as_of: datetime) -> None:
        """Periodic re-check so tiers drop when old spend leaves the window.

        Accounts already at the lowest tier are skipped: time passing can only
        lower spend, and upgrades are already handled when an order is placed.
        """
        self._advance_clock(as_of)
        base = self.tiers[0].tier_id
        for acc in self.accounts.values():
            if acc.current_tier_id != base:
                self._review_tier(acc, as_of, trigger_ref="TIER_REVIEW")

    # ----------------------------------------------------------------- queries

    def available_points(self, account_id: str, at: datetime) -> int:
        """Points that can be spent now: unexpired lots only, minus any debt."""
        lots = sum(self._lot_remaining[lot.txn_id] for lot in self._lots_by_account[account_id]
                   if lot.expires_on > at)
        debt = sum(owed for _, owed in self._debts[account_id])
        return lots - debt

    def qualifying_spend(self, account_id: str, as_of: datetime) -> Decimal:
        window = timedelta(days=int(self._rule("TIER_WINDOW", as_of).value))
        start = as_of - window
        return sum((self._net_qualifying[o] for at, o in self._orders_by_account[account_id]
                    if start < at <= as_of), Decimal("0"))

    # ---------------------------------------------------------------- internals

    def _post(self, acc: LoyaltyAccount, txn_type: str, points: int, at: datetime,
              **refs) -> PointsTransaction:
        """The ONLY place that writes to the ledger or changes a balance."""
        acc.points_balance += points
        txn = PointsTransaction(self.ids.next("PTS", at.year), acc.account_id, txn_type,
                                points, at, acc.points_balance, **refs)
        self.ledger.append(txn)
        return txn

    def _add_lot(self, txn: PointsTransaction) -> None:
        remaining = txn.points
        debts = self._debts[txn.account_id]
        while debts and remaining > 0:  # new points first repay earlier shortfalls
            debt = debts[0]
            take = min(debt[1], remaining)
            self.lot_allocations.append(LotAllocation(debt[0], txn.txn_id, take))
            debt[1] -= take
            remaining -= take
            if debt[1] == 0:
                debts.pop(0)
        self._lot_remaining[txn.txn_id] = remaining
        self._lots_by_account[txn.account_id].append(txn)
        self._open_lots.append(txn)

    def _consume(self, account_id: str, debit_txn_id: str, points: int, at: datetime,
                 first_lot: PointsTransaction | None = None) -> int:
        """Take points from lots oldest-first. Returns points that could not be covered."""
        need = points
        candidates = self._lots_by_account[account_id]
        if first_lot is not None:
            candidates = [first_lot] + candidates
        for lot in candidates:
            if need == 0:
                break
            if lot is not first_lot and lot.expires_on <= at:
                continue
            left = self._lot_remaining[lot.txn_id]
            if left <= 0:
                continue
            take = min(left, need)
            self._lot_remaining[lot.txn_id] = left - take
            self.lot_allocations.append(LotAllocation(debit_txn_id, lot.txn_id, take))
            need -= take
        return need

    def _review_tier(self, acc: LoyaltyAccount, at: datetime, trigger_ref: str) -> None:
        spend = self.qualifying_spend(acc.account_id, at)
        new_tier = [t for t in self.tiers if t.min_qualifying_spend <= spend][-1]
        if new_tier.tier_id != acc.current_tier_id:
            old_rank = self.tier_by_id[acc.current_tier_id].rank
            reason = "UPGRADE" if new_tier.rank > old_rank else "DOWNGRADE"
            self._record_tier_change(acc, acc.current_tier_id, new_tier.tier_id, at, spend,
                                     reason, trigger_ref)
            acc.current_tier_id = new_tier.tier_id

    def _record_tier_change(self, acc, from_tier, to_tier, at, spend, reason, trigger_ref):
        self.tier_changes.append(TierChange(self.ids.next("TCH", at.year), acc.account_id,
                                            from_tier, to_tier, at, spend, reason, trigger_ref))

    def _raise_exception(self, code, account_id, ref_id, at, detail):
        self.exceptions.append(ExceptionRecord(self.ids.next("EXC", at.year), code,
                                               account_id, ref_id, at, detail))

    def _rule(self, rule_type: str, at: datetime):
        for rule in self._rules_by_type[rule_type]:
            if rule.is_effective(at):
                return rule
        raise LookupError(f"no effective {rule_type} rule at {at}")

    def _earn_rule(self, tier_id: str, at: datetime):
        for rule in self._rules_by_type["EARN_RATE"]:
            if rule.tier_id == tier_id and rule.is_effective(at):
                return rule
        raise LookupError(f"no EARN_RATE rule for {tier_id} at {at}")

    def _category_rule(self, rule_type: str, category: str, at: datetime):
        for rule in self._rules_by_type[rule_type]:
            if rule.category == category and rule.is_effective(at):
                return rule
        return None

    def _advance_clock(self, at: datetime) -> None:
        if self._clock is not None and at < self._clock:
            raise ValueError(f"event at {at} is earlier than previous event at {self._clock}")
        self._clock = at


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))
