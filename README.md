# Loyalty Ledger

Accrue, redeem, expire and reverse loyalty points with a fully auditable,
append-only ledger, and generate a large, reproducible synthetic dataset from
the same rules.

> Status: **work in progress.** Rules engine and synthetic generator are done
> and tested. Anomaly injection, validation and full export are next (see Roadmap).

## Quick start

Requires Python 3.11+. From the `loyalty-ledger` folder:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e . pytest         # editable install: makes `import loyalty` work everywhere
python -m pytest -q
```

Generate data (CSV files land in `out/<scale>/`):

```bash
python -m loyalty.cli generate --scale starter            # 2,000 customers / 10,000 orders
python -m loyalty.cli generate --scale full               # 20,000 customers / 100,000 orders
python -m loyalty.cli generate --scale full --seed 42     # different seed
```

`--customers` / `--orders` override the preset sizes; `--seed` defaults to `20261022`.

## How it works

```
generator.py  ("Part A")  seeded timeline of what customers DO:
              sign-ups, orders, returns, redemption attempts, monthly jobs
        |  replayed in time order
        v
engine.py     ("Part B")  applies config.py rules, decides every OUTCOME:
              points, reversals, approvals/rejections, expiry, tiers
        |
        v
export.py     one CSV per table
```

The split matters: randomness only decides behaviour (who buys what, when,
what gets returned, which reward someone asks for). Balances, tiers,
approvals and expiries are never random - they come out of the rules, so the
data is internally consistent by construction.

### Customer behaviour model (generator.py)

| Aspect | Model |
|---|---|
| Customers | Segments OCCASIONAL 60% / REGULAR 30% / VIP 10% with 1x / 3x / 10x order frequency, plus log-normal individual variation |
| Sign-ups | Front-loaded in 2026 (exponential, mean 120 days), long tail to mid-2027 |
| Orders | Exactly N orders allocated by customer weight; seasonal (festive sale 1.8x, December 1.3x, weekends 1.2x); 08:00-22:00 |
| Baskets | 1-4 lines; 7 categories, 40 SKUs each with fixed shelf prices (log-uniform in a category range); 20% of lines discounted 5-30%; GST by category; INR 49 shipping on small online orders |
| Returns | Per-line probability by category (Apparel 15%, Footwear 10%, Electronics 5%, ...), 1-30 days later, 40% of multi-quantity returns are partial |
| Bonuses | Welcome bonus on joining; birthday bonus each year |
| Redemptions | Attempts 1-60 days after shopping. Redeemer styles HOARDER / NORMAL / EAGER. 92% pick an affordable reward (biased to cheaper), 8% try any reward and may be rejected. 2% double-click (two keys, same second), 1% network retry (same key) |
| Jobs | Expiry run + tier review on the 1st of every month |
| Window | 2026-01-01 to 2028-01-01, so year-one points expire during year two |

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
- Welcome bonus 200 pts, birthday bonus 300 pts; recorded as `BONUS` rows and
  treated as lots that expire like any other points.
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
src/loyalty/   config.py, models.py, ids.py, engine.py, generator.py, export.py, cli.py
tests/         test_engine_example.py (hand-worked example + edge cases)
               test_generator.py (reproducibility + rule invariants on generated data)
docs/          worked_example.md
ai_usage/      prompt history and AI-use disclosure
```

## Roadmap

- [x] Rules engine with hand-verified worked example
- [x] Synthetic generator (20,000 customers / 100,000 orders / ~300,000 points txns, fixed seed, scale parameter)
- [ ] Labelled anomaly injection + separate `ground_truth.csv`
- [ ] Validation suite (PK/FK, enums, dates, reconciliation, lifecycle, duplicates)
- [ ] Anomaly detector scored against ground truth (precision / recall)
- [ ] Export CSV / JSON / SQLite + SQL, manifest with checksums, reproducibility check
- [ ] Data dictionary and ER diagram
