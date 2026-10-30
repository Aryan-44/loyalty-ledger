# Loyalty Ledger

Accrue, redeem, expire and reverse loyalty points with a fully auditable,
append-only ledger, and generate a large, reproducible synthetic dataset from
the same rules.

> Status: **work in progress.** The rules engine is done and tested. The
> generator, anomaly injection, validation and export are next (see Roadmap).

## Quick start

Requires Python 3.11+. From the `loyalty-ledger` folder:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e . pytest         # editable install: makes `import loyalty` work everywhere
python -m pytest -q
```

## How it works

```
config.py (tiers, rules, rewards)
        |
        v
engine.py  -- one method per event: enroll / place_order / return_items /
              redeem / run_expiry / review_tiers. Each appends to the ledger.
```

Core design decisions:

| Decision | Why |
|---|---|
| Ledger is append-only; returns add a REVERSAL row pointing to the original ACCRUAL | Never overwrite history; every change is auditable |
| Every ACCRUAL is a *lot* with its own expiry date | Points can only expire correctly if we know which points are which |
| Debits consume lots oldest-first and are recorded in `lot_allocations` | Every point can be traced from earning to spending/expiry |
| One function (`_post`) writes ledger rows and updates the balance together | Balance cannot drift from the ledger sum |
| Rules are data in `config.py`, not hard-coded | An analyst can change rates/thresholds without touching logic |
| Events must arrive in time order (engine refuses otherwise) | Expiry and tier windows depend on time |

## Business rules and assumptions

- Qualifying spend = line price x quantity - line discount, for eligible
  categories. Tax and shipping never count. Gift cards are excluded.
- Earn rate depends on the tier held **at the time of purchase**
  (Bronze 10 / Silver 15 / Gold 20 / Platinum 25 pts per INR 100), floored per line.
- Electronics earn double points during the festive window (2026-10-15 to 2026-11-15).
- Tier = highest tier whose threshold is met by qualifying spend (net of returns)
  over the last 365 days. Re-checked after every order/return and by a periodic
  review, so tiers can go down as well as up.
- Points expire 365 days after they are earned. The expiry job is idempotent.
- Partial returns reverse points in proportion to quantity returned, taken from
  the original lot first. If the customer already spent those points, the balance
  goes negative, a `NEGATIVE_BALANCE_AFTER_RETURN` exception is raised, and the
  debt is repaid from the next accrual.
- Redemptions are checked against *available* points (unexpired lots minus debt),
  reward tier eligibility and reward status. Rejected attempts are still recorded.
  A repeated `request_key` returns the original result (no double debit).

## Project layout

```
src/loyalty/   config.py, models.py, ids.py, engine.py
tests/         test_engine_example.py (hand-worked example + edge cases)
docs/          worked_example.md
ai_usage/      prompt history and AI-use disclosure
```

## Roadmap

- [x] Rules engine with hand-verified worked example
- [ ] Synthetic generator (20,000 customers / 100,000 orders / 300,000 points txns, fixed seed, scale parameter)
- [ ] Labelled anomaly injection + separate `ground_truth.csv`
- [ ] Validation suite (PK/FK, enums, dates, reconciliation, lifecycle, duplicates)
- [ ] Anomaly detector scored against ground truth (precision / recall)
- [ ] Export CSV / JSON / SQLite + SQL, manifest with checksums, reproducibility check
- [ ] Data dictionary and ER diagram
