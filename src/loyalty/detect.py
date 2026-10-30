"""Detect anomalies from the data tables alone, then score against ground truth.

The detector never reads ground_truth: it recomputes what should be true from
raw records (ledger, lot allocations, order lines, returns, tier rules) and
reports every account where the stored data disagrees.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal


@dataclass(slots=True)
class Finding:
    issue_type: str
    account_id: str
    entity_id: str
    detail: str


def detect(tables: dict[str, list]) -> list[Finding]:
    ledger = tables["points_transactions"]
    findings: list[Finding] = []

    # 1. stored balance vs ledger sum
    ledger_sum = defaultdict(int)
    for t in ledger:
        ledger_sum[t.account_id] += t.points
    for acc in tables["loyalty_accounts"]:
        if acc.points_balance != ledger_sum[acc.account_id]:
            findings.append(Finding("BALANCE_MISMATCH", acc.account_id, acc.account_id,
                                    f"stored {acc.points_balance}, ledger {ledger_sum[acc.account_id]}"))

    # 2. redemption that spent points no lot could pay for
    covered = defaultdict(int)
    for a in tables["lot_allocations"]:
        covered[a.debit_txn_id] += a.points
    for t in ledger:
        if t.txn_type == "REDEMPTION" and covered[t.txn_id] < -t.points:
            findings.append(Finding("OVER_REDEMPTION", t.account_id, t.redemption_id,
                                    f"debited {-t.points}, only {covered[t.txn_id]} backed by lots"))

    # 3. returned lines whose points were not (fully) taken back
    owed = defaultdict(int)
    for line in tables["order_lines"]:
        if line.returned_qty and line.points_earned:
            owed[line.order_id] += line.points_earned * line.returned_qty // line.quantity
    reversed_pts = defaultdict(int)
    for t in ledger:
        if t.txn_type == "REVERSAL":
            reversed_pts[t.order_id] += -t.points
    orders = {o.order_id: o for o in tables["orders"]}
    for order_id, points in owed.items():
        if reversed_pts[order_id] < points:
            findings.append(Finding("RETURN_NOT_REVERSED", orders[order_id].account_id, order_id,
                                    f"should reverse {points}, reversed {reversed_pts[order_id]}"))

    # 4. a lot expired more than once / consumed beyond its size
    by_id = {t.txn_id: t for t in ledger}
    consumed, expiries = Counter(), Counter()
    for a in tables["lot_allocations"]:
        consumed[a.lot_txn_id] += a.points
        if by_id[a.debit_txn_id].txn_type == "EXPIRY":
            expiries[a.lot_txn_id] += 1
    for lot_id in consumed:
        lot = by_id[lot_id]
        if expiries[lot_id] > 1 or consumed[lot_id] > lot.points:
            findings.append(Finding("DOUBLE_EXPIRY", lot.account_id, lot_id,
                                    f"lot of {lot.points} expired {expiries[lot_id]}x, "
                                    f"consumed {consumed[lot_id]}"))

    # 5. tier change that the configured thresholds do not support
    findings.extend(_tier_findings(tables, orders))
    return findings


def _tier_findings(tables, orders) -> list[Finding]:
    tiers = sorted(tables["tiers"], key=lambda t: t.rank)
    window_rule = next(r for r in tables["rules"] if r.rule_type == "TIER_WINDOW")
    window = timedelta(days=int(window_rule.value))

    orders_by_acc = defaultdict(list)
    for o in orders.values():
        orders_by_acc[o.account_id].append(o)
    eligible = {l.line_id: l.eligible for l in tables["order_lines"]}
    refunds_by_acc = defaultdict(list)
    for r in tables["order_returns"]:
        if eligible[r.line_id]:
            o = orders[r.order_id]
            refunds_by_acc[o.account_id].append((r.returned_at, o.order_at, r.refund_amount))

    findings = []
    for change in tables["tier_changes"]:
        at, start = change.changed_at, change.changed_at - window
        spend = sum((o.qualifying_amount for o in orders_by_acc[change.account_id]
                     if start < o.order_at <= at), Decimal("0"))
        spend -= sum((amount for returned_at, order_at, amount in refunds_by_acc[change.account_id]
                      if returned_at <= at and start < order_at <= at), Decimal("0"))
        expected = [t for t in tiers if t.min_qualifying_spend <= spend][-1]
        if change.to_tier_id != expected.tier_id:
            findings.append(Finding("TIER_ON_GROSS_SPEND", change.account_id, change.tier_change_id,
                                    f"qualifying spend {spend} -> {expected.tier_id}, "
                                    f"assigned {change.to_tier_id}"))
    return findings


def score(findings: list[Finding], labels: list) -> dict:
    """Precision / recall per issue type, matched on (issue_type, account_id)."""
    found = {(f.issue_type, f.account_id) for f in findings}
    truth = {(g.issue_type, g.account_id) for g in labels}
    types = sorted({k[0] for k in found | truth})
    report = {}
    for issue in types + ["ALL"]:
        f = {k for k in found if issue in ("ALL", k[0])}
        g = {k for k in truth if issue in ("ALL", k[0])}
        tp = len(f & g)
        report[issue] = {
            "planted": len(g), "detected": len(f), "true_positive": tp,
            "false_positive": len(f - g), "false_negative": len(g - f),
            "precision": round(tp / len(f), 4) if f else 1.0,
            "recall": round(tp / len(g), 4) if g else 1.0,
        }
    return report
