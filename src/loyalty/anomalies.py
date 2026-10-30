"""Plant labelled anomalies into a clean, generated dataset.

Each anomaly imitates a realistic bug and touches only the records of one
target account; every other row stays correct. Target accounts are disjoint
across anomaly types so each planted issue is the only problem on its account.
What was planted is returned as ground-truth labels, which are written to a
separate file and never added as columns to the data tables.
"""
import bisect
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .engine import LoyaltyEngine
from .models import LotAllocation, PointsTransaction, TierChange, TransactionRule

ISSUE_TYPES = ["BALANCE_MISMATCH", "OVER_REDEMPTION", "RETURN_NOT_REVERSED",
               "DOUBLE_EXPIRY", "TIER_ON_GROSS_SPEND"]


@dataclass(slots=True)
class GroundTruth:
    issue_id: str
    issue_type: str
    account_id: str
    entity: str
    entity_id: str
    expected: str
    actual: str
    description: str


def default_per_type(customers: int) -> int:
    return max(5, customers // 200)


class _Injector:
    def __init__(self, engine: LoyaltyEngine, seed: int):
        self.e = engine
        self.rng = random.Random(f"{seed}-anomalies")
        self.labels: list[GroundTruth] = []
        self.used: set[str] = set()
        # accounts with legitimate exceptions are left alone so labels stay unambiguous
        self.used.update(x.account_id for x in engine.exceptions)
        self.rows = defaultdict(list)  # account -> its ledger rows in time order
        for t in engine.ledger:
            self.rows[t.account_id].append(t)
        self.removed_txns: set[str] = set()

    # ------------------------------------------------------------ helpers

    def _pick(self, candidates: list, account_of, n: int) -> list:
        """Seeded sample of up to n candidates on accounts not used yet."""
        pool = [c for c in candidates if account_of(c) not in self.used]
        self.rng.shuffle(pool)
        picked = []
        for c in pool:
            if len(picked) == n:
                break
            if account_of(c) not in self.used:
                self.used.add(account_of(c))
                picked.append(c)
        return picked

    def _label(self, issue_type, account_id, entity, entity_id, expected, actual, description):
        self.labels.append(GroundTruth(f"GT-{len(self.labels) + 1:06d}", issue_type, account_id,
                                       entity, entity_id, str(expected), str(actual), description))

    def _balance_before(self, account_id: str, at: datetime) -> int:
        balance = 0
        for t in self.rows[account_id]:
            if t.created_at > at:
                break
            balance = t.balance_after
        return balance

    def _shift_after(self, account_id: str, at: datetime, delta: int) -> None:
        """A row was added/removed at `at`: fix the running balance of later rows.

        Inserted rows go after every existing row with the same timestamp, so
        only strictly later rows move.
        """
        for t in self.rows[account_id]:
            if t.created_at > at:
                t.balance_after += delta
        self.e.accounts[account_id].points_balance += delta

    def _insert(self, txn: PointsTransaction) -> None:
        ledger = self.e.ledger
        ledger.insert(bisect.bisect_right(ledger, txn.created_at, key=lambda t: t.created_at), txn)
        rows = self.rows[txn.account_id]
        rows.insert(bisect.bisect_right(rows, txn.created_at, key=lambda t: t.created_at), txn)

    def _remove(self, txn: PointsTransaction) -> None:
        ledger = self.e.ledger
        i = bisect.bisect_left(ledger, txn.created_at, key=lambda t: t.created_at)
        while ledger[i] is not txn:
            i += 1
        del ledger[i]
        self.rows[txn.account_id].remove(txn)
        self.removed_txns.add(txn.txn_id)

    # ------------------------------------------------------------ anomalies

    def balance_mismatch(self, n: int) -> None:
        """Cached account balance drifts from the ledger (e.g. a failed cache update)."""
        for acc_id in self._pick(sorted(self.e.accounts), lambda a: a, n):
            acc = self.e.accounts[acc_id]
            delta = self.rng.choice([-1, 1]) * self.rng.randint(10, 500)
            ledger_sum = acc.points_balance
            acc.points_balance += delta
            self._label("BALANCE_MISMATCH", acc_id, "loyalty_accounts", acc_id,
                        f"points_balance={ledger_sum}", f"points_balance={acc.points_balance}",
                        f"Stored balance is off by {delta:+d} from the ledger sum")

    def over_redemption(self, n: int) -> None:
        """A correctly rejected redemption gets approved anyway (race-condition bug)."""
        candidates = [r for r in self.e.redemptions if r.reject_reason == "INSUFFICIENT_BALANCE"]
        for red in self._pick(candidates, lambda r: r.account_id, n):
            before = self._balance_before(red.account_id, red.requested_at)
            txn = PointsTransaction(self.e.ids.next("PTS", red.requested_at.year), red.account_id,
                                    "REDEMPTION", -red.points_cost, red.requested_at,
                                    before - red.points_cost, redemption_id=red.redemption_id)
            self._shift_after(red.account_id, red.requested_at, -red.points_cost)
            self._insert(txn)
            red.status, red.reject_reason, red.txn_id = "APPROVED", None, txn.txn_id
            self._label("OVER_REDEMPTION", red.account_id, "redemptions", red.redemption_id,
                        "REJECTED (insufficient available points)", "APPROVED",
                        f"Approved {red.points_cost} pts that the member did not have")

    def return_not_reversed(self, n: int) -> None:
        """A return is recorded but the points reversal is never posted."""
        allocs = defaultdict(list)
        for a in self.e.lot_allocations:
            allocs[a.debit_txn_id].append(a)
        returns_per_order = defaultdict(int)
        for r in self.e.order_returns:
            returns_per_order[r.order_id] += 1
        by_id = {t.txn_id: t for t in self.e.ledger}
        candidates = []
        for r in self.e.order_returns:
            if r.reversal_txn_id is None or returns_per_order[r.order_id] != 1:
                continue
            rev = by_id[r.reversal_txn_id]
            own = allocs[rev.txn_id]
            # simple case only: reversal fully taken back from its own lot
            if len(own) == 1 and own[0].lot_txn_id == rev.reverses_txn_id and own[0].points == -rev.points:
                candidates.append(r)
        lines = {l.line_id: l for l in self.e.order_lines}
        for ret in self._pick(candidates, lambda r: self.e.orders[r.order_id].account_id, n):
            rev = by_id[ret.reversal_txn_id]
            self._remove(rev)
            self._shift_after(rev.account_id, rev.created_at, -rev.points)
            lines[ret.line_id].points_reversed = 0
            ret.reversal_txn_id = None
            self._label("RETURN_NOT_REVERSED", rev.account_id, "order_returns", ret.return_id,
                        f"REVERSAL of {-rev.points} pts", "no reversal posted",
                        f"Order {ret.order_id} returned but member kept {-rev.points} pts")

    def double_expiry(self, n: int) -> None:
        """The next monthly job expires an already-expired lot a second time."""
        batches = sorted(self.e.expiry_batches, key=lambda b: b.run_at)
        next_batch = {b.batch_id: batches[i + 1] for i, b in enumerate(batches[:-1])}
        lot_of = {a.debit_txn_id: a.lot_txn_id for a in self.e.lot_allocations}
        candidates = [t for t in self.e.ledger
                      if t.txn_type == "EXPIRY" and t.expiry_batch_id in next_batch]
        expiry_rule = self.e._rule("POINTS_EXPIRY", batches[0].run_at).rule_id
        for first in self._pick(candidates, lambda t: t.account_id, n):
            batch = next_batch[first.expiry_batch_id]
            before = self._balance_before(first.account_id, batch.run_at)
            txn = PointsTransaction(self.e.ids.next("PTS", batch.run_at.year), first.account_id,
                                    "EXPIRY", first.points, batch.run_at, before + first.points,
                                    expiry_batch_id=batch.batch_id)
            self._shift_after(first.account_id, batch.run_at, first.points)
            self._insert(txn)
            self.e.lot_allocations.append(LotAllocation(txn.txn_id, lot_of[first.txn_id], -first.points))
            self.e.transaction_rules.append(TransactionRule(txn.txn_id, expiry_rule))
            self._label("DOUBLE_EXPIRY", first.account_id, "points_transactions", txn.txn_id,
                        "lot expired once", f"lot {lot_of[first.txn_id]} expired again",
                        f"{-first.points} pts expired a second time in {batch.batch_id}")

    def tier_on_gross_spend(self, n: int) -> None:
        """Final tier review sums gross order totals instead of qualifying spend."""
        as_of = max(b.run_at for b in self.e.expiry_batches)
        window_start = as_of - timedelta(days=int(self.e._rule("TIER_WINDOW", as_of).value))
        gross = defaultdict(int)
        for o in self.e.orders.values():
            if window_start < o.order_at <= as_of:
                gross[o.account_id] += o.total_paid  # includes tax, shipping, gift cards, returns
        tier_for = lambda spend: [t for t in self.e.tiers if t.min_qualifying_spend <= spend][-1]
        candidates = []
        for acc_id in sorted(gross):
            acc = self.e.accounts[acc_id]
            wrong = tier_for(gross[acc_id])
            if wrong.rank > self.e.tier_by_id[acc.current_tier_id].rank:
                candidates.append(acc_id)
        for acc_id in self._pick(candidates, lambda a: a, n):
            acc = self.e.accounts[acc_id]
            wrong = tier_for(gross[acc_id])
            right = acc.current_tier_id
            change = TierChange(self.e.ids.next("TCH", as_of.year), acc_id, right, wrong.tier_id,
                                as_of, gross[acc_id], "UPGRADE", "TIER_REVIEW")
            self.e.tier_changes.append(change)
            acc.current_tier_id = wrong.tier_id
            self._label("TIER_ON_GROSS_SPEND", acc_id, "tier_changes", change.tier_change_id,
                        right, wrong.tier_id,
                        f"Upgraded on gross spend {gross[acc_id]} instead of qualifying "
                        f"{self.e.qualifying_spend(acc_id, as_of)}")

    def finish(self) -> None:
        if self.removed_txns:
            self.e.lot_allocations[:] = [a for a in self.e.lot_allocations
                                         if a.debit_txn_id not in self.removed_txns]
        # the buggy expiry job reports what it did, so batch totals follow the ledger
        stats = defaultdict(lambda: [0, set(), 0])
        for t in self.e.ledger:
            if t.txn_type == "EXPIRY":
                s = stats[t.expiry_batch_id]
                s[0] += 1
                s[1].add(t.account_id)
                s[2] += -t.points
        for b in self.e.expiry_batches:
            lots, accounts, points = stats.get(b.batch_id, [0, set(), 0])
            b.lots_expired, b.accounts_affected, b.points_expired = lots, len(accounts), points


def inject(engine: LoyaltyEngine, seed: int, per_type: int) -> list[GroundTruth]:
    inj = _Injector(engine, seed)
    inj.balance_mismatch(per_type)
    inj.over_redemption(per_type)
    inj.return_not_reversed(per_type)
    inj.double_expiry(per_type)
    inj.tier_on_gross_spend(per_type)
    inj.finish()
    return inj.labels
