# Loyalty Ledger

Accrue, redeem, expire and reverse loyalty points with a fully auditable,
append-only ledger, and generate a large, reproducible synthetic dataset from
the same rules.

> Status: every part of the brief is implemented and tested (see
> [How the brief is covered](#how-the-brief-is-covered)).

**Documentation:** [data model + ER diagram](docs/data_model.md) ·
[data dictionary](docs/data_dictionary.md) ·
[worked example](docs/worked_example.md) ·
[SQL schema](src/loyalty/schema.sql) ·
[AI usage](ai_usage/AI_USAGE.md) · [prompt history](ai_usage/prompt_history.md)

## Quick start

Requires Python 3.11+. From the `loyalty-ledger` folder:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e . pytest         # editable install: makes `import loyalty` work everywhere
python -m pytest -q
```

Generate data:

```bash
python -m loyalty.cli generate --scale starter            # 2,000 customers / 10,000 orders
python -m loyalty.cli generate --scale full               # 20,000 customers / 100,000 orders
python -m loyalty.cli generate --scale full --seed 42     # different seed
python -m loyalty.cli validate out/starter                # re-check files on disk (+ checksums)
python -m loyalty.cli reproduce --scale starter           # build twice, compare every checksum
```

`generate` and `validate` exit with code 1 if any validation check FAILs
(`validate` also fails if any file differs from `manifest.json`); `reproduce`
exits with 1 if any file differs between the two runs.

`--customers` / `--orders` override the preset sizes; `--seed` defaults to `20261022`;
`--anomalies-per-type N` sets how many issues are planted (default customers/200, min 5);
`--no-anomalies` produces clean data only; `--formats csv,json,sqlite,sql` picks
outputs (default all - a full-scale build with every format is ~800 MB, so
`--formats csv` is handy when space matters).

Output layout - the answer key is deliberately kept apart from the data:

```
out/<scale>/README.md                        what is in the folder and the command that made it
out/<scale>/manifest.json                    command, seed, provenance, row counts, SHA-256 of every file
out/<scale>/data/*.csv                       the dataset (15 tables)
out/<scale>/json/*.json                      same tables as JSON (money as exact decimal strings)
out/<scale>/sql/loyalty.db                   SQLite, every PK / FK / CHECK constraint enforced
out/<scale>/sql/loyalty_seed.sql             schema + INSERTs, replay with: sqlite3 new.db < loyalty_seed.sql
out/<scale>/sql/schema.sql                   the DDL on its own
out/<scale>/labels/ground_truth.csv|.json    planted issues (never joined into data)
out/<scale>/reports/validation_summary.md    every validation check with PASS / EXPECTED / FAIL
out/<scale>/reports/validation_results.json  same, machine-readable, with sample violating IDs
out/<scale>/reports/findings.csv             what the detector flagged
out/<scale>/reports/detection_metrics.json   precision / recall per issue type
```

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
anomalies.py  plants labelled bugs on a few accounts -> ground_truth
        |
        v
export.py     data/ and labels/ written separately
detect.py     finds issues from data/ only, scored against labels/
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

### Planted anomalies and how they are detected

Valid data is generated first; anomalies are injected afterwards. Each one
imitates a realistic bug, touches only one target account, and is the only
issue on that account (targets are disjoint, and accounts with legitimate
exceptions are excluded). Running balances (`balance_after`) are kept
consistent so nothing unrelated is corrupted.

| Issue type | How it is planted | How the detector finds it (data only) |
|---|---|---|
| `BALANCE_MISMATCH` | Cached `points_balance` shifted by +/-10..500; ledger untouched | Stored balance != sum of ledger points |
| `OVER_REDEMPTION` | A redemption correctly rejected for insufficient points is flipped to APPROVED and debited (race-condition bug) | Redemption debit not fully backed by `lot_allocations` |
| `RETURN_NOT_REVERSED` | A return is recorded but its REVERSAL row is never posted | Points owed back from `order_lines.returned_qty` > reversed points |
| `DOUBLE_EXPIRY` | Next month's expiry job expires an already-expired lot again | A lot with 2+ EXPIRY allocations or consumed beyond its size |
| `TIER_ON_GROSS_SPEND` | Final tier review uses gross order totals (tax, shipping, gift cards, returns) and upgrades the member | Tier change disagrees with tier recomputed from qualifying spend and configured thresholds |

**Evaluation:** findings are matched to ground truth on `(issue_type, account_id)`
and reported as precision / recall per type. The detector scores 1.0 / 1.0 on
the generated data, and finds **nothing** on clean data. That is expected for
a rule-based detector checking deterministic rule violations: its job here is
to prove every planted issue is observable from the data alone and that the
baseline is clean. It would not catch issues outside these five rules.

### Validation suite (validate.py)

75 checks that read the data tables only. Each returns the rows that break it.

| Category | Examples |
|---|---|
| Primary keys (PK, ID) | every table's key present and unique (incl. composite keys of bridge tables); ID formats; `LOY-<customer-id>`; 1:1 customer-account; year inside IDs matches the record date |
| Foreign keys (FK) | 26 relationships incl. optional ones (`reverses_txn_id`, `redemption.txn_id`, ...); a return's line belongs to its order |
| Allowed values (EN) | status/type/reason enums; points sign by type; each transaction type carries exactly its required references; quantity/price/discount ranges |
| Date order (DT) | no activity before enrolment; returns after orders; lot expiry = earn date + 365 days and never expired early; ledger in time order; lots earned before they are spent |
| Reconciliation (AR) | order totals; **line points recomputed from the rules table**; one accrual per order; running balances; balance = ledger; debits fully backed by lots; lot never over-consumed; reversals = owed points; batch totals |
| Lifecycle (LC) | order status vs returned quantities; redemption APPROVED/REJECTED state; reversal links; expire-once; tier history chain; tier follows thresholds |
| Duplicates (DU) | unique emails, redemption request keys, one debit per redemption, no identical ledger rows |

Because the dataset contains planted issues on purpose, each result is classified:

- **PASS** - no violations.
- **EXPECTED** - violations exist, but every one is on an account labelled
  with the planted issue the check is designed to catch.
- **FAIL** - at least one violation is not explained by a label: a real bug.

On generated data: clean runs give 75 PASS; with anomalies, 69 PASS +
6 EXPECTED + 0 FAIL. Tests also prove the negative cases: the same data
without labels gives 6 FAIL, and an unlabelled balance drift, broken foreign
key, duplicate ID or wrong points calculation each produce FAIL.

`validate` re-reads the CSV files from disk with their types, so it checks
the actual deliverable, not just in-memory objects.

### Export, manifest and reproducibility (export.py, schema.sql)

- **CSV / JSON:** one file per table, parents before children. JSON keeps
  integers and booleans native and money as exact decimal strings (no float rounding).
- **SQLite:** loaded with `PRAGMA foreign_keys = ON` and every key, uniqueness
  and CHECK rule from `schema.sql`. Foreign keys are `DEFERRABLE INITIALLY
  DEFERRED` because two links are circular (redemption <-> its ledger row,
  reversal -> accrual), so the whole load is one transaction checked at
  COMMIT - the database itself refuses data with a broken key or an
  impossible value (tests prove both). Every foreign-key column is indexed:
  the first full-scale load hung because SQLite scans an unindexed child
  column for every parent insert (quadratic); with indexes the load time grows
  linearly (measured ~9x time for 10x rows).
- **SQL seed script:** schema + INSERTs in parent-to-child order, written from
  the in-memory rows. (SQLite's own `iterdump` orders tables alphabetically and
  inserts children before their parents exist - found and fixed while testing
  the replay.) Replaying it gives a database identical row-for-row to `loyalty.db`.
- **manifest.json:** canonical regenerate command, seed, parameters, Python
  version, provenance (100% synthetic, no external data), row counts and
  columns per table, and size + SHA-256 of every file. It has **no wall-clock
  timestamp** on purpose so that it is itself reproducible.
- **reproduce:** builds the dataset twice into separate folders and compares
  every file's checksum and every table's row count; writes
  `reproducibility_report.json`. Verified at full scale: all 40 files (~0.8 GB,
  including the 205 MB SQLite database and 284 MB SQL seed script) are
  byte-identical across two runs.

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
src/loyalty/   config.py, models.py, ids.py, engine.py, generator.py,
               anomalies.py, detect.py, validate.py, export.py, cli.py, schema.sql
tests/         test_engine_example.py (hand-worked example + edge cases)
               test_generator.py (reproducibility + rule invariants on generated data)
               test_anomalies.py (labels isolated, no leakage, all planted issues detected)
               test_validate.py (clean passes, planted = EXPECTED, real bugs FAIL, CSV round trip)
               test_export.py (byte-identical reruns, SQLite constraints, SQL replay, tamper detection)
               test_docs.py (every column documented, cited check IDs exist, ER covers all tables)
docs/          data_model.md (ER diagram, cardinality, lifecycles), data_dictionary.md,
               worked_example.md
ai_usage/      prompt history and AI-use disclosure
```

## How the brief is covered

| Brief requirement | Where | Proof |
|---|---|---|
| Accrue, redeem, expire, reverse with an auditable ledger | `engine.py`; append-only `points_transactions` + `lot_allocations` | `test_engine_example.py`, checks AR-04..AR-10, LC-03, LC-04 |
| Multiple related entities, PK/FK, cardinality, optional links, effective dates, lifecycles, bridge tables | `schema.sql`, [data_model.md](docs/data_model.md) | SQLite load with constraints ON; PK-*, FK-* checks |
| Conditional rules | tier earn rates, category exclusion/multiplier with effective dates, reward tier gates, redemption checks | AR-02 recomputes every line's points from `rules` |
| Exception path | rejected redemptions; return after points were spent -> negative balance + `exceptions` row + debt repaid by next accrual | `test_return_after_points_spent_creates_debt_and_exception` |
| Step 1 - data dictionary | [data_dictionary.md](docs/data_dictionary.md) | `test_docs.py` keeps it in sync with the code |
| Steps 2-3 - parents first, domain logic, deterministic IDs | `generator.py` -> `engine.py`, `ids.py` | ID-01..ID-03, FK-* |
| Step 4 - 20k customers / 100k orders / 300k txns, refunds, partial returns, expiry, tier changes, concurrent redemptions, fixed seed, scale parameter | `generator.py`, `--scale`, `--seed` | full run: 20,000 / 100,000 / 303,391; `test_edge_cases_are_present` |
| Step 5 - labelled edge cases (5 types) | `anomalies.py` | `test_anomalies.py` |
| Step 6 - derived values from source records | balances, totals, tiers, statuses all computed by the engine | AR-01, AR-02, AR-04, LC-01, LC-06 |
| Step 7 - separate ground truth, no leakage | `labels/ground_truth.csv` and `.json` | `test_labels_never_leak_into_data_tables` |
| Step 8 - validation | `validate.py`, 75 checks | full run: 69 PASS, 6 EXPECTED, 0 FAIL |
| Step 9 - CSV/JSON/SQL, README, dictionary, command, seed, manifest, validation summary | `export.py`, `out/<scale>/` | `test_export.py` |
| Step 10 - regenerate twice, compare | `reproduce` command | full scale: 40 files byte-identical |
| Measurable outputs / evaluation metrics | detector precision/recall; KPIs printed by `generate` | 500/500 found, 0 false positives |

## Limitations and next steps

- **Single-threaded simulation.** Concurrency is modelled as same-second and
  retried requests processed in order; a production system would need
  database transactions/row locks around the balance check.
- **Rule-based detector.** It is exact for the five documented issue types and
  would not find other kinds of corruption; a scored or ML detector could be
  evaluated against the same ground truth.
- **Tier downgrades are checked monthly**, not daily, and points are earned at
  the tier held at purchase time (documented assumptions).
- **Behaviour is plausible, not calibrated** against real retail data;
  distributions (segments, seasonality, return rates) are documented
  assumptions in `generator.py`.
- **Single currency / time zone** (INR, naive local time).
- **Performance:** pure Python. Generating + validating the full dataset took
  ~1.5 minutes on the development laptop (CSV only); writing every format
  adds a few minutes and ~0.8 GB. `--scale starter` runs in seconds. Faster
  generation would need vectorised generation or a compiled engine.
