# Data dictionary

Every column of every exported table. Relationships, cardinality and
lifecycles are in [data_model.md](data_model.md).

**Conventions**

- **Types:** `text`, `int`, `decimal(12,2)` (money, exact), `datetime`
  (`YYYY-MM-DD HH:MM:SS`, naive local time), `bool` (CSV `true`/`false`,
  SQLite 0/1, JSON true/false).
- **Units:** money in **INR**; points are whole **pts**; durations in days.
- **Req:** `Y` required (never empty), `N` optional (empty in CSV, `NULL` in SQL,
  `null` in JSON), `C` conditional (rule in the Validation column).
- **Unique:** `PK` primary key, `UK` unique, `PK*` part of a composite key.
- **Validation:** the check ID in `reports/validation_summary.md`
  (`src/loyalty/validate.py`) and/or the SQL constraint in `schema.sql` that
  enforces the rule. `EN-01` covers every listed enum.

---

### `tiers`
Tier definitions. Master data, 4 rows.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `tier_id` | text | Y | `TIER-BRONZE`, `TIER-SILVER`, `TIER-GOLD`, `TIER-PLATINUM` | PK | PK-01 |
| `name` | text | Y | Bronze, Silver, Gold, Platinum | UK | SQL UNIQUE |
| `rank` | int | Y | 1-4, higher = better | UK | SQL CHECK `rank >= 1`, UNIQUE |
| `min_qualifying_spend` | decimal | Y | INR in the rolling tier window: 0 / 20,000 / 50,000 / 100,000 | | SQL CHECK `>= 0`; used by LC-06 |
| `effective_from` | datetime | Y | 2026-01-01 | | |

### `rules`
Business rules as data. The engine reads these; validation recomputes points from them.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `rule_id` | text | Y | `RULE-001`... | PK | PK-02 |
| `rule_type` | text | Y | `EARN_RATE`, `CATEGORY_EXCLUSION`, `CATEGORY_MULTIPLIER`, `POINTS_EXPIRY`, `TIER_WINDOW`, `ENROLLMENT_BONUS`, `BIRTHDAY_BONUS` | | EN-01, SQL CHECK |
| `description` | text | Y | free text | | |
| `value` | decimal | Y | meaning depends on type: pts per INR 100 (EARN_RATE), multiplier (CATEGORY_MULTIPLIER), days (POINTS_EXPIRY, TIER_WINDOW), pts (bonuses), 0 (EXCLUSION) | | AR-02 |
| `effective_from` | datetime | Y | rule applies from this time (inclusive) | | |
| `effective_to` | datetime | N | rule stops at this time (exclusive); empty = open-ended | | SQL CHECK `> effective_from` |
| `tier_id` | text | C | required for `EARN_RATE`, empty otherwise | | FK-03 -> `tiers` |
| `category` | text | C | required for `CATEGORY_*` rules, empty otherwise | | |

### `rewards`
Reward catalogue, 8 rows.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `reward_id` | text | Y | `RWD-001`... | PK | PK-03 |
| `name` | text | Y | free text | | |
| `points_cost` | int | Y | pts, > 0 (100 - 8,000) | | SQL CHECK `> 0` |
| `min_tier_id` | text | N | empty = any tier | | FK-04 -> `tiers`; redemption rejected `TIER_NOT_ELIGIBLE` below it |
| `active` | bool | Y | true / false | | SQL CHECK |

### `customers`
Synthetic people. No real personal data.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `customer_id` | text | Y | `C` + 6 digits, e.g. `C000001` | PK | PK-04, ID-01 |
| `first_name` | text | Y | from a built-in list | | |
| `last_name` | text | Y | from a built-in list | | |
| `email` | text | Y | `first.last.n@example.com` (reserved domain) | UK | DU-01, SQL UNIQUE |
| `city` | text | Y | one of 12 Indian cities | | |
| `signup_date` | datetime | Y | 2026-01-01 plus 0-540 days (front-loaded) | | DT-01 |
| `birth_date` | datetime | N | 1956-2008; before `signup_date` | | DT-01, SQL CHECK |

### `loyalty_accounts`
One programme account per customer.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `account_id` | text | Y | `LOY-<customer_id>` | PK | PK-05, ID-01, ID-02, SQL CHECK |
| `customer_id` | text | Y | | UK | FK-01 -> `customers`; ID-02 (1:1) |
| `enrolled_at` | datetime | Y | = customer `signup_date` | | DT-01 |
| `status` | text | Y | `ACTIVE` | | EN-01 |
| `current_tier_id` | text | Y | a tier ID | | FK-02; LC-05 (equals last `tier_changes.to_tier_id`) |
| `points_balance` | int | Y | pts; cached copy, may be negative after a return of spent points | | **AR-05: must equal the ledger sum** |

### `orders`
One purchase. Header totals are derived from the lines, never generated independently.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `order_id` | text | Y | `ORD-YYYY-NNNNNN` (year of `order_at`) | PK | PK-06, ID-01, ID-03 |
| `account_id` | text | Y | | | FK-05 |
| `order_at` | datetime | Y | after enrolment; mostly 08:00-22:00 | | DT-02 |
| `channel` | text | Y | `ONLINE`, `STORE` | | EN-01 |
| `status` | text | Y | `COMPLETED`, `PARTIALLY_RETURNED`, `RETURNED` | | EN-01, LC-01 |
| `merchandise_amount` | decimal | Y | INR = sum of line `net_amount` | | AR-01 |
| `qualifying_amount` | decimal | Y | INR = sum of **eligible** line `net_amount` (excludes tax, shipping, gift cards); before returns | | AR-01, SQL CHECK `<= merchandise` |
| `tax_amount` | decimal | Y | INR, GST by category (0-18%) | | SQL CHECK `>= 0` |
| `shipping_amount` | decimal | Y | INR; 49 on online orders under 499, else 0 | | SQL CHECK `>= 0` |
| `total_paid` | decimal | Y | INR = merchandise + tax + shipping | | AR-01 |
| `tier_id_at_order` | text | Y | tier held at purchase; sets the earn rate | | FK-06, AR-02 |

### `order_lines`
Items in an order.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `line_id` | text | Y | `<order_id>-NN` | PK | PK-07 |
| `order_id` | text | Y | | | FK-07 |
| `line_no` | int | Y | 1-4 | UK with `order_id` | SQL UNIQUE `(order_id, line_no)` |
| `sku` | text | Y | `SKU-<CAT>-NNNN`, 40 per category | | |
| `category` | text | Y | Electronics, Apparel, Footwear, Home, Beauty, Grocery, Gift Cards | | |
| `quantity` | int | Y | 1-3 | | EN-04 |
| `unit_price` | decimal | Y | INR shelf price, > 0 | | EN-04 |
| `discount_amount` | decimal | Y | INR, 0 to unit_price x quantity | | EN-04 |
| `net_amount` | decimal | Y | INR = unit_price x quantity - discount | | AR-02 |
| `eligible` | bool | Y | false only for excluded categories (Gift Cards) | | AR-02 |
| `points_earned` | int | Y | pts = floor(net x tier rate x multiplier / 100); 0 if not eligible | | **AR-02 (recomputed from `rules`)** |
| `returned_qty` | int | Y | 0 to quantity | | EN-04, LC-01 |
| `points_reversed` | int | Y | pts = floor(points_earned x returned_qty / quantity) | | SQL CHECK `<= points_earned`; AR-08 |

### `order_returns`
Event table: one row per return of (part of) a line.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `return_id` | text | Y | `RET-YYYY-NNNNNN` | PK | PK-08 |
| `order_id` | text | Y | | | FK-08 |
| `line_id` | text | Y | must belong to `order_id` | | FK-09, FK-90 |
| `quantity` | int | Y | >= 1 | | SQL CHECK |
| `refund_amount` | decimal | Y | INR = line net x quantity / line quantity | | AR-08 |
| `returned_at` | datetime | Y | 1-30 days after the order | | DT-03 |
| `reversal_txn_id` | text | C | the REVERSAL row; empty when no points are due back (e.g. gift-card line) | | FK-10, DT-03, DU-03, AR-08 |

### `points_transactions`
**The ledger.** Append-only; every points change is one signed row.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `txn_id` | text | Y | `PTS-YYYY-NNNNNNNN` (year of `created_at`) | PK | PK-11, ID-01, ID-03 |
| `account_id` | text | Y | | | FK-14 |
| `txn_type` | text | Y | `ACCRUAL`, `BONUS`, `REVERSAL`, `REDEMPTION`, `EXPIRY`, `ADJUSTMENT` (reserved, unused) | | EN-01 |
| `points` | int | Y | pts, signed: > 0 for ACCRUAL/BONUS, < 0 for REVERSAL/REDEMPTION/EXPIRY | | EN-02, SQL CHECK |
| `created_at` | datetime | Y | rows stored in time order | | DT-02, DT-05 |
| `balance_after` | int | Y | pts = running sum of the account's rows up to this one | | AR-04 |
| `order_id` | text | C | required for ACCRUAL and REVERSAL, empty otherwise | | FK-15, EN-03, AR-03 |
| `redemption_id` | text | C | required for REDEMPTION, empty otherwise | | FK-16, EN-03, DU-03 |
| `expiry_batch_id` | text | C | required for EXPIRY, empty otherwise | | FK-17, EN-03 |
| `reverses_txn_id` | text | C | required for REVERSAL: the ACCRUAL of the same order | | FK-18, EN-03, LC-03 |
| `expires_on` | datetime | C | required for ACCRUAL/BONUS (a *lot*) = created_at + 365 days; empty otherwise | | EN-03, DT-04, SQL CHECK |

### `lot_allocations`
Bridge (N:M): which accrual lots paid for each debit. Makes every point traceable.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `debit_txn_id` | text | Y | a REDEMPTION, EXPIRY or REVERSAL row | PK* | PK-14, FK-19 |
| `lot_txn_id` | text | Y | an ACCRUAL or BONUS row | PK* | PK-14, FK-20, DT-06 |
| `points` | int | Y | pts taken from that lot, > 0 | | AR-06, AR-07, AR-10, LC-04 |

### `transaction_rules`
Bridge (N:M): which rules decided each transaction (earn rate, exclusion, multiplier, expiry, bonus).

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `txn_id` | text | Y | | PK* | PK-15, FK-21 |
| `rule_id` | text | Y | | PK* | PK-15, FK-22 |

### `redemptions`
Every redemption attempt, approved or rejected.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `redemption_id` | text | Y | `RDM-YYYY-NNNNNN` | PK | PK-09, ID-01, ID-03 |
| `account_id` | text | Y | | | FK-11 |
| `reward_id` | text | Y | | | FK-12 |
| `points_cost` | int | Y | pts = reward cost at request time | | LC-02 |
| `requested_at` | datetime | Y | after enrolment | | DT-02 |
| `request_key` | text | Y | client idempotency key; a retry with the same key returns the original result | UK | DU-02 |
| `status` | text | Y | `APPROVED`, `REJECTED` | | EN-01, LC-02 |
| `reject_reason` | text | C | required iff REJECTED: `INSUFFICIENT_BALANCE`, `TIER_NOT_ELIGIBLE`, `REWARD_INACTIVE` | | EN-01, LC-02, SQL CHECK |
| `txn_id` | text | C | required iff APPROVED: its REDEMPTION ledger row | UK | FK-13, LC-02, SQL CHECK |

### `expiry_batches`
One row per monthly expiry run (1st of each month, 00:00).

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `batch_id` | text | Y | `EXP-YYYY-NNNNNN` | PK | PK-10, ID-01 |
| `run_at` | datetime | Y | 1st of the month 00:00 | UK | SQL UNIQUE |
| `lots_expired` | int | Y | count of EXPIRY rows in the run | | AR-09 |
| `accounts_affected` | int | Y | distinct accounts, <= lots_expired | | SQL CHECK |
| `points_expired` | int | Y | pts, sum of the run's EXPIRY rows (positive) | | AR-09 |

### `tier_changes`
Tier history: every enrolment, upgrade and downgrade.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `tier_change_id` | text | Y | `TCH-YYYY-NNNNNN` | PK | PK-12 |
| `account_id` | text | Y | | | FK-23 |
| `from_tier_id` | text | C | empty iff `reason = ENROLLED` | | FK-24, LC-05, SQL CHECK |
| `to_tier_id` | text | Y | | | FK-25, LC-05, LC-06 |
| `changed_at` | datetime | Y | non-decreasing per account | | LC-05 |
| `qualifying_spend_12m` | decimal | Y | INR qualifying spend (net of returns) in the 365 days up to `changed_at` | | LC-06 |
| `reason` | text | Y | `ENROLLED`, `UPGRADE`, `DOWNGRADE` | | EN-01, LC-05 |
| `trigger_ref` | text | N | order ID, return ID or `TIER_REVIEW`; empty for ENROLLED | | |

### `exceptions`
Operations-manager queue of business exceptions.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `exception_id` | text | Y | `EXC-YYYY-NNNNNN` | PK | PK-13 |
| `code` | text | Y | `NEGATIVE_BALANCE_AFTER_RETURN` | | EN-01 |
| `account_id` | text | Y | | | FK-26 |
| `ref_id` | text | Y | the return ID that caused it | | |
| `occurred_at` | datetime | Y | | | |
| `detail` | text | Y | free text, e.g. points already spent and resulting balance | | |

---

### `ground_truth` (labels/, not part of the dataset)
Answer key for planted anomalies. Stored separately and never joined to the
tables above.

| Field | Type | Req | Allowed values / units | Unique | Validation |
|---|---|---|---|---|---|
| `issue_id` | text | Y | `GT-NNNNNN` | PK | |
| `issue_type` | text | Y | `BALANCE_MISMATCH`, `OVER_REDEMPTION`, `RETURN_NOT_REVERSED`, `DOUBLE_EXPIRY`, `TIER_ON_GROSS_SPEND` | | |
| `account_id` | text | Y | exactly one label per account | UK | test_anomalies |
| `entity` | text | Y | table holding the faulty record | | |
| `entity_id` | text | Y | ID of the faulty record | | |
| `expected` | text | Y | what correct data would show | | |
| `actual` | text | Y | what the planted data shows | | |
| `description` | text | Y | human-readable explanation | | |
