"""Validation suite for the generated dataset.

Every check reads the data tables only and returns the rows that break it.
Results are then classified against the ground-truth labels:

    PASS      no violations
    EXPECTED  violations exist, but every one is on an account labelled with
              the planted issue this check is meant to catch
    FAIL      at least one violation is not explained by a label -> a real bug

Categories follow the brief: primary keys, foreign keys, allowed values,
date ordering, arithmetic reconciliation, lifecycle transitions, duplicates.
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

CHECKS = []


def check(check_id, category, description, explains=None):
    def register(fn):
        CHECKS.append((check_id, category, description, explains, fn))
        return fn
    return register


@dataclass
class CheckResult:
    check_id: str
    category: str
    description: str
    rows_checked: int
    violations: int
    status: str
    explains: str | None = None
    sample: list = field(default_factory=list)


class Ctx:
    """Tables plus lookups shared by the checks."""

    def __init__(self, tables: dict[str, list]):
        self.t = tables
        self.ledger = tables["points_transactions"]
        self.txn = {x.txn_id: x for x in self.ledger}
        self.accounts = {a.account_id: a for a in tables["loyalty_accounts"]}
        self.customers = {c.customer_id: c for c in tables["customers"]}
        self.orders = {o.order_id: o for o in tables["orders"]}
        self.lines = {l.line_id: l for l in tables["order_lines"]}
        self.tiers = {t.tier_id: t for t in tables["tiers"]}
        self.tier_list = sorted(tables["tiers"], key=lambda t: t.rank)
        self.rules = tables["rules"]
        self.redemptions = {r.redemption_id: r for r in tables["redemptions"]}
        self.lines_by_order = defaultdict(list)
        for l in tables["order_lines"]:
            self.lines_by_order[l.order_id].append(l)
        self.allocated = Counter()   # debit txn -> points covered by lots
        self.consumed = Counter()    # lot txn -> points taken from it
        self.expiries = Counter()    # lot txn -> number of EXPIRY debits
        for a in tables["lot_allocations"]:
            self.allocated[a.debit_txn_id] += a.points
            self.consumed[a.lot_txn_id] += a.points
            if a.debit_txn_id in self.txn and self.txn[a.debit_txn_id].txn_type == "EXPIRY":
                self.expiries[a.lot_txn_id] += 1

    def rule_value(self, rule_type):
        return next(r.value for r in self.rules if r.rule_type == rule_type)

    def account_of_order(self, order_id):
        o = self.orders.get(order_id)
        return o.account_id if o else None


# ------------------------------------------------------------------ primary keys

PRIMARY_KEYS = [
    ("tiers", "tier_id"), ("rules", "rule_id"), ("rewards", "reward_id"),
    ("customers", "customer_id"), ("loyalty_accounts", "account_id"),
    ("orders", "order_id"), ("order_lines", "line_id"), ("order_returns", "return_id"),
    ("redemptions", "redemption_id"), ("expiry_batches", "batch_id"),
    ("points_transactions", "txn_id"), ("tier_changes", "tier_change_id"),
    ("exceptions", "exception_id"),
    ("lot_allocations", ("debit_txn_id", "lot_txn_id")),
    ("transaction_rules", ("txn_id", "rule_id")),
]


def _pk_check(table, key):
    cols = key if isinstance(key, tuple) else (key,)

    def fn(ctx):
        rows = ctx.t[table]
        seen = Counter(tuple(getattr(r, c) for c in cols) for r in rows)
        bad = [(None, "|".join(map(str, k))) for k, n in seen.items() if n > 1 or None in k]
        return len(rows), bad
    return fn


for i, (table, key) in enumerate(PRIMARY_KEYS, start=1):
    name = "+".join(key) if isinstance(key, tuple) else key
    check(f"PK-{i:02d}", "primary_key", f"{table}.{name} is present and unique")(_pk_check(table, key))


ID_FORMATS = [
    ("customers", "customer_id", r"C\d{6}"),
    ("loyalty_accounts", "account_id", r"LOY-C\d{6}"),
    ("orders", "order_id", r"ORD-\d{4}-\d{6}"),
    ("points_transactions", "txn_id", r"PTS-\d{4}-\d{8}"),
    ("redemptions", "redemption_id", r"RDM-\d{4}-\d{6}"),
    ("expiry_batches", "batch_id", r"EXP-\d{4}-\d{6}"),
]


@check("ID-01", "primary_key", "IDs follow the documented formats (LOY-, ORD-, PTS-, RDM-, EXP-)")
def _id_formats(ctx):
    bad, n = [], 0
    for table, col, pattern in ID_FORMATS:
        rx = re.compile(pattern)
        for r in ctx.t[table]:
            n += 1
            if not rx.fullmatch(getattr(r, col)):
                bad.append((None, getattr(r, col)))
    return n, bad


@check("ID-02", "primary_key", "account_id is LOY-<customer_id> and every customer has exactly one account (1:1)")
def _account_ids(ctx):
    bad = [(a.account_id, a.account_id) for a in ctx.accounts.values()
           if a.account_id != f"LOY-{a.customer_id}"]
    owners = Counter(a.customer_id for a in ctx.accounts.values())
    bad += [(None, c) for c in ctx.customers if owners[c] != 1]
    return len(ctx.accounts), bad


@check("ID-03", "primary_key", "year inside ORD/PTS/RDM IDs matches the record's own date")
def _id_years(ctx):
    pairs = ([(o.order_id, o.order_at) for o in ctx.orders.values()]
             + [(t.txn_id, t.created_at) for t in ctx.ledger]
             + [(r.redemption_id, r.requested_at) for r in ctx.redemptions.values()])
    return len(pairs), [(None, i) for i, at in pairs if int(i.split("-")[1]) != at.year]


# ------------------------------------------------------------------ foreign keys

FOREIGN_KEYS = [  # child table, column, parent table, parent key, optional
    ("loyalty_accounts", "customer_id", "customers", "customer_id", False),
    ("loyalty_accounts", "current_tier_id", "tiers", "tier_id", False),
    ("rules", "tier_id", "tiers", "tier_id", True),
    ("rewards", "min_tier_id", "tiers", "tier_id", True),
    ("orders", "account_id", "loyalty_accounts", "account_id", False),
    ("orders", "tier_id_at_order", "tiers", "tier_id", False),
    ("order_lines", "order_id", "orders", "order_id", False),
    ("order_returns", "order_id", "orders", "order_id", False),
    ("order_returns", "line_id", "order_lines", "line_id", False),
    ("order_returns", "reversal_txn_id", "points_transactions", "txn_id", True),
    ("redemptions", "account_id", "loyalty_accounts", "account_id", False),
    ("redemptions", "reward_id", "rewards", "reward_id", False),
    ("redemptions", "txn_id", "points_transactions", "txn_id", True),
    ("points_transactions", "account_id", "loyalty_accounts", "account_id", False),
    ("points_transactions", "order_id", "orders", "order_id", True),
    ("points_transactions", "redemption_id", "redemptions", "redemption_id", True),
    ("points_transactions", "expiry_batch_id", "expiry_batches", "batch_id", True),
    ("points_transactions", "reverses_txn_id", "points_transactions", "txn_id", True),
    ("lot_allocations", "debit_txn_id", "points_transactions", "txn_id", False),
    ("lot_allocations", "lot_txn_id", "points_transactions", "txn_id", False),
    ("transaction_rules", "txn_id", "points_transactions", "txn_id", False),
    ("transaction_rules", "rule_id", "rules", "rule_id", False),
    ("tier_changes", "account_id", "loyalty_accounts", "account_id", False),
    ("tier_changes", "from_tier_id", "tiers", "tier_id", True),
    ("tier_changes", "to_tier_id", "tiers", "tier_id", False),
    ("exceptions", "account_id", "loyalty_accounts", "account_id", False),
]


def _fk_check(child, col, parent, key, optional):
    def fn(ctx):
        keys = {getattr(p, key) for p in ctx.t[parent]}
        bad = []
        for r in ctx.t[child]:
            v = getattr(r, col)
            if (v is None and not optional) or (v is not None and v not in keys):
                bad.append((getattr(r, "account_id", None), f"{col}={v}"))
        return len(ctx.t[child]), bad
    return fn


for i, (child, col, parent, key, optional) in enumerate(FOREIGN_KEYS, start=1):
    check(f"FK-{i:02d}", "foreign_key",
          f"{child}.{col} -> {parent}.{key}{' (optional)' if optional else ''}")(
        _fk_check(child, col, parent, key, optional))


@check("FK-90", "foreign_key", "a return's line belongs to the returned order")
def _return_line_order(ctx):
    rows = ctx.t["order_returns"]
    bad = [(ctx.account_of_order(r.order_id), r.return_id) for r in rows
           if r.line_id in ctx.lines and ctx.lines[r.line_id].order_id != r.order_id]
    return len(rows), bad


# ------------------------------------------------------------------ allowed values

ENUMS = [
    ("loyalty_accounts", "status", {"ACTIVE"}),
    ("orders", "status", {"COMPLETED", "PARTIALLY_RETURNED", "RETURNED"}),
    ("orders", "channel", {"ONLINE", "STORE"}),
    ("points_transactions", "txn_type", {"ACCRUAL", "BONUS", "REVERSAL", "REDEMPTION", "EXPIRY", "ADJUSTMENT"}),
    ("redemptions", "status", {"APPROVED", "REJECTED"}),
    ("redemptions", "reject_reason", {None, "INSUFFICIENT_BALANCE", "TIER_NOT_ELIGIBLE", "REWARD_INACTIVE"}),
    ("tier_changes", "reason", {"ENROLLED", "UPGRADE", "DOWNGRADE"}),
    ("rules", "rule_type", {"EARN_RATE", "CATEGORY_EXCLUSION", "CATEGORY_MULTIPLIER", "POINTS_EXPIRY",
                            "TIER_WINDOW", "ENROLLMENT_BONUS", "BIRTHDAY_BONUS"}),
    ("exceptions", "code", {"NEGATIVE_BALANCE_AFTER_RETURN"}),
]


@check("EN-01", "allowed_values", "status / type / reason columns only use documented values")
def _enums(ctx):
    bad, n = [], 0
    for table, col, allowed in ENUMS:
        for r in ctx.t[table]:
            n += 1
            if getattr(r, col) not in allowed:
                bad.append((getattr(r, "account_id", None), f"{table}.{col}={getattr(r, col)}"))
    return n, bad


@check("EN-02", "allowed_values", "points sign matches type (earn > 0, spend/expire/reverse < 0)")
def _signs(ctx):
    positive = {"ACCRUAL", "BONUS"}
    bad = [(t.account_id, t.txn_id) for t in ctx.ledger
           if ((t.points <= 0) if t.txn_type in positive else (t.points >= 0))]
    return len(ctx.ledger), bad


@check("EN-03", "allowed_values", "each transaction type carries exactly its required references")
def _txn_refs(ctx):
    need = {  # type: (order, redemption, batch, reverses, expires)
        "ACCRUAL": (1, 0, 0, 0, 1), "BONUS": (0, 0, 0, 0, 1), "REVERSAL": (1, 0, 0, 1, 0),
        "REDEMPTION": (0, 1, 0, 0, 0), "EXPIRY": (0, 0, 1, 0, 0),
    }
    bad = []
    for t in ctx.ledger:
        has = tuple(int(v is not None) for v in
                    (t.order_id, t.redemption_id, t.expiry_batch_id, t.reverses_txn_id, t.expires_on))
        if need.get(t.txn_type) != has:
            bad.append((t.account_id, t.txn_id))
    return len(ctx.ledger), bad


@check("EN-04", "allowed_values", "order lines: quantity >= 1, price > 0, 0 <= discount <= gross, returned_qty <= quantity")
def _line_values(ctx):
    bad = [(ctx.account_of_order(l.order_id), l.line_id) for l in ctx.lines.values()
           if l.quantity < 1 or l.unit_price <= 0 or l.discount_amount < 0
           or l.discount_amount > l.unit_price * l.quantity
           or not 0 <= l.returned_qty <= l.quantity]
    return len(ctx.lines), bad


# ------------------------------------------------------------------ date ordering

@check("DT-01", "date_order", "account enrolment is on/after customer sign-up; birth date before sign-up")
def _enrol_dates(ctx):
    bad = []
    for a in ctx.accounts.values():
        c = ctx.customers.get(a.customer_id)
        if c and (a.enrolled_at < c.signup_date or (c.birth_date and c.birth_date >= c.signup_date)):
            bad.append((a.account_id, a.account_id))
    return len(ctx.accounts), bad


@check("DT-02", "date_order", "no order, redemption or ledger row before the member enrolled")
def _after_enrol(ctx):
    rows = ([(o.account_id, o.order_id, o.order_at) for o in ctx.orders.values()]
            + [(r.account_id, r.redemption_id, r.requested_at) for r in ctx.redemptions.values()]
            + [(t.account_id, t.txn_id, t.created_at) for t in ctx.ledger])
    bad = [(acc, i) for acc, i, at in rows
           if acc in ctx.accounts and at < ctx.accounts[acc].enrolled_at]
    return len(rows), bad


@check("DT-03", "date_order", "returns happen after the order and post their reversal at the same time")
def _return_dates(ctx):
    bad = []
    for r in ctx.t["order_returns"]:
        o = ctx.orders[r.order_id]
        rev = ctx.txn.get(r.reversal_txn_id) if r.reversal_txn_id else None
        if r.returned_at <= o.order_at or (rev and rev.created_at != r.returned_at):
            bad.append((o.account_id, r.return_id))
    return len(ctx.t["order_returns"]), bad


@check("DT-04", "date_order", "lots expire exactly POINTS_EXPIRY days after earning; expiry posted on/after that date")
def _expiry_dates(ctx):
    days = timedelta(days=int(ctx.rule_value("POINTS_EXPIRY")))
    bad, n = [], 0
    for t in ctx.ledger:
        if t.expires_on is not None:
            n += 1
            if t.expires_on != t.created_at + days:
                bad.append((t.account_id, t.txn_id))
    for a in ctx.t["lot_allocations"]:
        debit, lot = ctx.txn.get(a.debit_txn_id), ctx.txn.get(a.lot_txn_id)
        if debit and lot and debit.txn_type == "EXPIRY":
            n += 1
            if debit.created_at < lot.expires_on:
                bad.append((debit.account_id, debit.txn_id))
    return n, bad


@check("DT-05", "date_order", "ledger is stored in time order and reversals come after what they reverse")
def _ledger_order(ctx):
    bad = [(b.account_id, b.txn_id) for a, b in zip(ctx.ledger, ctx.ledger[1:])
           if b.created_at < a.created_at]
    for t in ctx.ledger:
        if t.reverses_txn_id in ctx.txn and ctx.txn[t.reverses_txn_id].created_at > t.created_at:
            bad.append((t.account_id, t.txn_id))
    return len(ctx.ledger), bad


@check("DT-06", "date_order", "debits only use lots that were earned before them")
def _lot_before_debit(ctx):
    rows = ctx.t["lot_allocations"]
    bad = [(ctx.txn[a.debit_txn_id].account_id, a.debit_txn_id) for a in rows
           if a.debit_txn_id in ctx.txn and a.lot_txn_id in ctx.txn
           and ctx.txn[a.lot_txn_id].created_at > ctx.txn[a.debit_txn_id].created_at
           and ctx.txn[a.debit_txn_id].txn_type != "REVERSAL"]  # debt repaid by later lots
    return len(rows), bad


# ------------------------------------------------------------------ arithmetic reconciliation

@check("AR-01", "reconciliation", "order totals: merchandise = sum(lines), qualifying = eligible lines, paid = merch + tax + shipping")
def _order_totals(ctx):
    bad = []
    for o in ctx.orders.values():
        lines = ctx.lines_by_order[o.order_id]
        merch = sum((l.net_amount for l in lines), Decimal("0"))
        qual = sum((l.net_amount for l in lines if l.eligible), Decimal("0"))
        if (o.merchandise_amount != merch or o.qualifying_amount != qual
                or o.total_paid != o.merchandise_amount + o.tax_amount + o.shipping_amount):
            bad.append((o.account_id, o.order_id))
    return len(ctx.orders), bad


@check("AR-02", "reconciliation", "line net = price x qty - discount; points = floor(net x tier rate x multiplier / 100)")
def _line_points(ctx):
    earn = {r.tier_id: r for r in ctx.rules if r.rule_type == "EARN_RATE"}
    excluded = {r.category for r in ctx.rules if r.rule_type == "CATEGORY_EXCLUSION"}
    multipliers = [r for r in ctx.rules if r.rule_type == "CATEGORY_MULTIPLIER"]
    bad = []
    for l in ctx.lines.values():
        o = ctx.orders[l.order_id]
        expected = 0
        if l.category not in excluded:
            mult = Decimal("1")
            for m in multipliers:
                if m.category == l.category and m.is_effective(o.order_at):
                    mult = m.value
            rate = earn[o.tier_id_at_order].value
            expected = int((l.net_amount * rate * mult / 100).to_integral_value(rounding=ROUND_FLOOR))
        if (l.net_amount != l.unit_price * l.quantity - l.discount_amount
                or l.eligible == (l.category in excluded) or l.points_earned != expected):
            bad.append((o.account_id, l.line_id))
    return len(ctx.lines), bad


@check("AR-03", "reconciliation", "each order's ACCRUAL equals the sum of its line points (one accrual per order)")
def _accrual_per_order(ctx):
    accruals = defaultdict(list)
    for t in ctx.ledger:
        if t.txn_type == "ACCRUAL":
            accruals[t.order_id].append(t.points)
    bad = []
    for o in ctx.orders.values():
        pts = sum(l.points_earned for l in ctx.lines_by_order[o.order_id])
        got = accruals.get(o.order_id, [])
        if (pts == 0 and got) or (pts > 0 and got != [pts]):
            bad.append((o.account_id, o.order_id))
    return len(ctx.orders), bad


@check("AR-04", "reconciliation", "running balance_after = cumulative sum of points per account")
def _running_balance(ctx):
    running = defaultdict(int)
    bad = []
    for t in ctx.ledger:
        running[t.account_id] += t.points
        if t.balance_after != running[t.account_id]:
            bad.append((t.account_id, t.txn_id))
    return len(ctx.ledger), bad


@check("AR-05", "reconciliation", "stored account balance = sum of its ledger", explains="BALANCE_MISMATCH")
def _balance_vs_ledger(ctx):
    sums = defaultdict(int)
    for t in ctx.ledger:
        sums[t.account_id] += t.points
    bad = [(a.account_id, a.account_id) for a in ctx.accounts.values()
           if a.points_balance != sums[a.account_id]]
    return len(ctx.accounts), bad


@check("AR-06", "reconciliation", "every redemption debit is fully paid by earned lots", explains="OVER_REDEMPTION")
def _redemption_backed(ctx):
    rows = [t for t in ctx.ledger if t.txn_type == "REDEMPTION"]
    return len(rows), [(t.account_id, t.redemption_id) for t in rows if ctx.allocated[t.txn_id] != -t.points]


@check("AR-07", "reconciliation", "no lot is consumed beyond its size", explains="DOUBLE_EXPIRY")
def _lot_size(ctx):
    lots = [t for t in ctx.ledger if t.txn_type in ("ACCRUAL", "BONUS")]
    return len(lots), [(t.account_id, t.txn_id) for t in lots if ctx.consumed[t.txn_id] > t.points]


@check("AR-08", "reconciliation", "returned lines: refund = net x qty/quantity, points reversed = floor(points x returned/qty)",
       explains="RETURN_NOT_REVERSED")
def _returns_reversed(ctx):
    refunds = defaultdict(Decimal)
    for r in ctx.t["order_returns"]:
        line = ctx.lines[r.line_id]
        if r.refund_amount != (line.net_amount * r.quantity / line.quantity).quantize(Decimal("0.01")):
            refunds["bad"] += 1
    owed, reversed_pts = defaultdict(int), defaultdict(int)
    for l in ctx.lines.values():
        if l.returned_qty:
            owed[l.order_id] += l.points_earned * l.returned_qty // l.quantity
    for t in ctx.ledger:
        if t.txn_type == "REVERSAL":
            reversed_pts[t.order_id] += -t.points
    bad = [(ctx.account_of_order(o), o) for o in owed if owed[o] != reversed_pts[o]]
    bad += [(None, "refund_amount")] * int(refunds["bad"])
    return len(owed), bad


@check("AR-09", "reconciliation", "expiry batch totals = sum of that batch's EXPIRY rows")
def _batch_totals(ctx):
    pts, lots = Counter(), Counter()
    for t in ctx.ledger:
        if t.txn_type == "EXPIRY":
            pts[t.expiry_batch_id] += -t.points
            lots[t.expiry_batch_id] += 1
    rows = ctx.t["expiry_batches"]
    return len(rows), [(None, b.batch_id) for b in rows
                       if b.points_expired != pts[b.batch_id] or b.lots_expired != lots[b.batch_id]]


@check("AR-10", "reconciliation", "every EXPIRY is traced to exactly the lot points it removed")
def _expiries_traced(ctx):
    rows = [t for t in ctx.ledger if t.txn_type == "EXPIRY"]
    return len(rows), [(t.account_id, t.txn_id) for t in rows if ctx.allocated[t.txn_id] != -t.points]


# ------------------------------------------------------------------ lifecycle

@check("LC-01", "lifecycle", "order status matches its lines (COMPLETED / PARTIALLY_RETURNED / RETURNED)")
def _order_status(ctx):
    bad = []
    for o in ctx.orders.values():
        lines = ctx.lines_by_order[o.order_id]
        if all(l.returned_qty == 0 for l in lines):
            expected = "COMPLETED"
        elif all(l.returned_qty == l.quantity for l in lines):
            expected = "RETURNED"
        else:
            expected = "PARTIALLY_RETURNED"
        if o.status != expected:
            bad.append((o.account_id, o.order_id))
    return len(ctx.orders), bad


@check("LC-02", "lifecycle", "APPROVED redemptions have a ledger debit and no reason; REJECTED have a reason and no debit")
def _redemption_state(ctx):
    bad = []
    for r in ctx.redemptions.values():
        ok = ((r.status == "APPROVED" and r.txn_id and not r.reject_reason)
              or (r.status == "REJECTED" and not r.txn_id and r.reject_reason))
        if not ok or (r.txn_id and ctx.txn.get(r.txn_id) and
                      ctx.txn[r.txn_id].points != -r.points_cost):
            bad.append((r.account_id, r.redemption_id))
    return len(ctx.redemptions), bad


@check("LC-03", "lifecycle", "reversals point to the ACCRUAL of the same order")
def _reversal_links(ctx):
    rows = [t for t in ctx.ledger if t.txn_type == "REVERSAL"]
    bad = [(t.account_id, t.txn_id) for t in rows
           if ctx.txn.get(t.reverses_txn_id) is None
           or ctx.txn[t.reverses_txn_id].txn_type != "ACCRUAL"
           or ctx.txn[t.reverses_txn_id].order_id != t.order_id]
    return len(rows), bad


@check("LC-04", "lifecycle", "each lot expires at most once (expiry is idempotent)", explains="DOUBLE_EXPIRY")
def _expire_once(ctx):
    return len(ctx.expiries), [(ctx.txn[lot].account_id, lot) for lot, n in ctx.expiries.items() if n > 1]


@check("LC-05", "lifecycle", "tier history is a valid chain: starts ENROLLED, from = previous to, "
                             "UPGRADE raises rank, DOWNGRADE lowers it, last = current tier")
def _tier_chain(ctx):
    history = defaultdict(list)
    for c in ctx.t["tier_changes"]:
        history[c.account_id].append(c)
    bad = []
    for acc_id, acc in ctx.accounts.items():
        chain = history.get(acc_id, [])
        ok = bool(chain) and chain[0].reason == "ENROLLED" and chain[0].from_tier_id is None
        for prev, cur in zip(chain, chain[1:]):
            step = ctx.tiers[cur.to_tier_id].rank - ctx.tiers[cur.from_tier_id].rank
            ok &= (cur.from_tier_id == prev.to_tier_id and cur.changed_at >= prev.changed_at
                   and ((cur.reason == "UPGRADE" and step > 0) or (cur.reason == "DOWNGRADE" and step < 0)))
        if not ok or chain[-1].to_tier_id != acc.current_tier_id:
            bad.append((acc_id, acc_id))
    return len(ctx.accounts), bad


@check("LC-06", "lifecycle", "every tier change follows configured thresholds on qualifying spend (net of returns)",
       explains="TIER_ON_GROSS_SPEND")
def _tier_thresholds(ctx):
    window = timedelta(days=int(ctx.rule_value("TIER_WINDOW")))
    orders_by_acc = defaultdict(list)
    for o in ctx.orders.values():
        orders_by_acc[o.account_id].append(o)
    refunds = defaultdict(list)
    for r in ctx.t["order_returns"]:
        if ctx.lines[r.line_id].eligible:
            o = ctx.orders[r.order_id]
            refunds[o.account_id].append((r.returned_at, o.order_at, r.refund_amount))
    bad = []
    for c in ctx.t["tier_changes"]:
        start = c.changed_at - window
        spend = sum((o.qualifying_amount for o in orders_by_acc[c.account_id]
                     if start < o.order_at <= c.changed_at), Decimal("0"))
        spend -= sum((amt for ret_at, ord_at, amt in refunds[c.account_id]
                      if ret_at <= c.changed_at and start < ord_at <= c.changed_at), Decimal("0"))
        expected = [t for t in ctx.tier_list if t.min_qualifying_spend <= spend][-1]
        if c.to_tier_id != expected.tier_id or c.qualifying_spend_12m != spend:
            bad.append((c.account_id, c.tier_change_id))
    return len(ctx.t["tier_changes"]), bad


# ------------------------------------------------------------------ duplicates

@check("DU-01", "duplicates", "customer emails are unique")
def _emails(ctx):
    seen = Counter(c.email for c in ctx.customers.values())
    return len(seen), [(None, e) for e, n in seen.items() if n > 1]


@check("DU-02", "duplicates", "redemption request keys are unique (retries did not create a second redemption)")
def _request_keys(ctx):
    seen = Counter(r.request_key for r in ctx.redemptions.values())
    return len(seen), [(None, k) for k, n in seen.items() if n > 1]


@check("DU-03", "duplicates", "a redemption has at most one ledger debit; an order at most one reversal per return")
def _single_debit(ctx):
    debits = Counter(t.redemption_id for t in ctx.ledger if t.txn_type == "REDEMPTION")
    bad = [(ctx.redemptions[r].account_id, r) for r, n in debits.items() if n > 1]
    linked = Counter(r.reversal_txn_id for r in ctx.t["order_returns"] if r.reversal_txn_id)
    bad += [(ctx.txn[t].account_id, t) for t, n in linked.items() if n > 1]
    return len(debits) + len(linked), bad


@check("DU-04", "duplicates", "no identical ledger rows (same account, type, points, time and references)")
def _dup_rows(ctx):
    seen = Counter((t.account_id, t.txn_type, t.points, t.created_at, t.order_id, t.redemption_id,
                    t.expiry_batch_id, t.reverses_txn_id) for t in ctx.ledger
                   if t.txn_type != "EXPIRY")  # several lots may expire in one batch
    return len(ctx.ledger), [(k[0], f"{k[1]}@{k[3]}") for k, n in seen.items() if n > 1]


# ------------------------------------------------------------------ runner

def run_validation(tables: dict[str, list], labels: list | None = None) -> list[CheckResult]:
    ctx = Ctx(tables)
    labelled = defaultdict(set)
    for g in labels or []:
        labelled[g.issue_type].add(g.account_id)
    results = []
    for check_id, category, description, explains, fn in CHECKS:
        checked, bad = fn(ctx)
        if not bad:
            status = "PASS"
        elif explains and all(acc in labelled[explains] for acc, _ in bad):
            status = "EXPECTED"
        else:
            status = "FAIL"
        results.append(CheckResult(check_id, category, description, checked, len(bad), status,
                                   explains, [entity for _, entity in bad[:5]]))
    return results


def summary_markdown(results: list[CheckResult]) -> str:
    counts = Counter(r.status for r in results)
    lines = ["# Validation summary", "",
             f"{len(results)} checks: **{counts['PASS']} PASS**, **{counts['EXPECTED']} EXPECTED** "
             f"(violations all on accounts labelled with the planted issue), **{counts['FAIL']} FAIL**.",
             "", "| Check | Category | Rule | Rows checked | Violations | Status |",
             "|---|---|---|---:|---:|---|"]
    for r in results:
        note = f" (planted `{r.explains}`)" if r.status == "EXPECTED" else ""
        lines.append(f"| {r.check_id} | {r.category} | {r.description} | {r.rows_checked:,} | "
                     f"{r.violations:,} | {r.status}{note} |")
    return "\n".join(lines) + "\n"
