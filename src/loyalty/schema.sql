-- Loyalty ledger schema (SQLite; standard SQL apart from DEFERRABLE syntax).
-- Money: NUMERIC(12,2) in INR. Points: INTEGER. Timestamps: ISO-8601 TEXT.
-- Booleans: INTEGER 0/1. All foreign keys are deferred so the circular links
-- (redemption <-> ledger row, reversal -> accrual) are checked at COMMIT.

CREATE TABLE tiers (
    tier_id              TEXT PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE,
    rank                 INTEGER NOT NULL UNIQUE CHECK (rank >= 1),
    min_qualifying_spend NUMERIC(12,2) NOT NULL CHECK (min_qualifying_spend >= 0),
    effective_from       TEXT NOT NULL
);

CREATE TABLE rules (
    rule_id        TEXT PRIMARY KEY,
    rule_type      TEXT NOT NULL CHECK (rule_type IN ('EARN_RATE', 'CATEGORY_EXCLUSION',
                       'CATEGORY_MULTIPLIER', 'POINTS_EXPIRY', 'TIER_WINDOW',
                       'ENROLLMENT_BONUS', 'BIRTHDAY_BONUS')),
    description    TEXT NOT NULL,
    value          NUMERIC(12,2) NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to   TEXT CHECK (effective_to IS NULL OR effective_to > effective_from),
    tier_id        TEXT REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED,
    category       TEXT
);

CREATE TABLE rewards (
    reward_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    points_cost INTEGER NOT NULL CHECK (points_cost > 0),
    min_tier_id TEXT REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED,
    active      INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    city        TEXT NOT NULL,
    signup_date TEXT NOT NULL,
    birth_date  TEXT CHECK (birth_date IS NULL OR birth_date < signup_date)
);

CREATE TABLE loyalty_accounts (
    account_id      TEXT PRIMARY KEY CHECK (account_id = 'LOY-' || customer_id),
    customer_id     TEXT NOT NULL UNIQUE REFERENCES customers (customer_id) DEFERRABLE INITIALLY DEFERRED,
    enrolled_at     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ACTIVE')),
    current_tier_id TEXT NOT NULL REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED,
    points_balance  INTEGER NOT NULL
);

CREATE TABLE orders (
    order_id           TEXT PRIMARY KEY,
    account_id         TEXT NOT NULL REFERENCES loyalty_accounts (account_id) DEFERRABLE INITIALLY DEFERRED,
    order_at           TEXT NOT NULL,
    channel            TEXT NOT NULL CHECK (channel IN ('ONLINE', 'STORE')),
    status             TEXT NOT NULL CHECK (status IN ('COMPLETED', 'PARTIALLY_RETURNED', 'RETURNED')),
    merchandise_amount NUMERIC(12,2) NOT NULL CHECK (merchandise_amount >= 0),
    qualifying_amount  NUMERIC(12,2) NOT NULL CHECK (qualifying_amount BETWEEN 0 AND merchandise_amount),
    tax_amount         NUMERIC(12,2) NOT NULL CHECK (tax_amount >= 0),
    shipping_amount    NUMERIC(12,2) NOT NULL CHECK (shipping_amount >= 0),
    total_paid         NUMERIC(12,2) NOT NULL,
    tier_id_at_order   TEXT NOT NULL REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE order_lines (
    line_id         TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL REFERENCES orders (order_id) DEFERRABLE INITIALLY DEFERRED,
    line_no         INTEGER NOT NULL CHECK (line_no >= 1),
    sku             TEXT NOT NULL,
    category        TEXT NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    discount_amount NUMERIC(12,2) NOT NULL CHECK (discount_amount >= 0),
    net_amount      NUMERIC(12,2) NOT NULL CHECK (net_amount >= 0),
    eligible        INTEGER NOT NULL CHECK (eligible IN (0, 1)),
    points_earned   INTEGER NOT NULL CHECK (points_earned >= 0),
    returned_qty    INTEGER NOT NULL CHECK (returned_qty BETWEEN 0 AND quantity),
    points_reversed INTEGER NOT NULL CHECK (points_reversed BETWEEN 0 AND points_earned),
    UNIQUE (order_id, line_no)
);

CREATE TABLE order_returns (
    return_id       TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL REFERENCES orders (order_id) DEFERRABLE INITIALLY DEFERRED,
    line_id         TEXT NOT NULL REFERENCES order_lines (line_id) DEFERRABLE INITIALLY DEFERRED,
    quantity        INTEGER NOT NULL CHECK (quantity >= 1),
    refund_amount   NUMERIC(12,2) NOT NULL CHECK (refund_amount >= 0),
    returned_at     TEXT NOT NULL,
    reversal_txn_id TEXT REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE redemptions (
    redemption_id TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL REFERENCES loyalty_accounts (account_id) DEFERRABLE INITIALLY DEFERRED,
    reward_id     TEXT NOT NULL REFERENCES rewards (reward_id) DEFERRABLE INITIALLY DEFERRED,
    points_cost   INTEGER NOT NULL CHECK (points_cost > 0),
    requested_at  TEXT NOT NULL,
    request_key   TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL CHECK (status IN ('APPROVED', 'REJECTED')),
    reject_reason TEXT CHECK (reject_reason IN ('INSUFFICIENT_BALANCE', 'TIER_NOT_ELIGIBLE', 'REWARD_INACTIVE')),
    txn_id        TEXT UNIQUE REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((status = 'APPROVED' AND txn_id IS NOT NULL AND reject_reason IS NULL)
        OR (status = 'REJECTED' AND txn_id IS NULL AND reject_reason IS NOT NULL))
);

CREATE TABLE expiry_batches (
    batch_id          TEXT PRIMARY KEY,
    run_at            TEXT NOT NULL UNIQUE,
    lots_expired      INTEGER NOT NULL CHECK (lots_expired >= 0),
    accounts_affected INTEGER NOT NULL CHECK (accounts_affected BETWEEN 0 AND lots_expired),
    points_expired    INTEGER NOT NULL CHECK (points_expired >= 0)
);

CREATE TABLE points_transactions (
    txn_id          TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES loyalty_accounts (account_id) DEFERRABLE INITIALLY DEFERRED,
    txn_type        TEXT NOT NULL CHECK (txn_type IN ('ACCRUAL', 'BONUS', 'REVERSAL', 'REDEMPTION',
                        'EXPIRY', 'ADJUSTMENT')),
    points          INTEGER NOT NULL CHECK (
                        (txn_type IN ('ACCRUAL', 'BONUS') AND points > 0)
                        OR (txn_type IN ('REVERSAL', 'REDEMPTION', 'EXPIRY') AND points < 0)
                        OR (txn_type = 'ADJUSTMENT' AND points <> 0)),
    created_at      TEXT NOT NULL,
    balance_after   INTEGER NOT NULL,
    order_id        TEXT REFERENCES orders (order_id) DEFERRABLE INITIALLY DEFERRED,
    redemption_id   TEXT REFERENCES redemptions (redemption_id) DEFERRABLE INITIALLY DEFERRED,
    expiry_batch_id TEXT REFERENCES expiry_batches (batch_id) DEFERRABLE INITIALLY DEFERRED,
    reverses_txn_id TEXT REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED,
    expires_on      TEXT CHECK ((expires_on IS NOT NULL) = (txn_type IN ('ACCRUAL', 'BONUS')))
);

CREATE TABLE lot_allocations (
    debit_txn_id TEXT NOT NULL REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED,
    lot_txn_id   TEXT NOT NULL REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED,
    points       INTEGER NOT NULL CHECK (points > 0),
    PRIMARY KEY (debit_txn_id, lot_txn_id)
);

CREATE TABLE transaction_rules (
    txn_id  TEXT NOT NULL REFERENCES points_transactions (txn_id) DEFERRABLE INITIALLY DEFERRED,
    rule_id TEXT NOT NULL REFERENCES rules (rule_id) DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY (txn_id, rule_id)
);

CREATE TABLE tier_changes (
    tier_change_id       TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES loyalty_accounts (account_id) DEFERRABLE INITIALLY DEFERRED,
    from_tier_id         TEXT REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED,
    to_tier_id           TEXT NOT NULL REFERENCES tiers (tier_id) DEFERRABLE INITIALLY DEFERRED,
    changed_at           TEXT NOT NULL,
    qualifying_spend_12m NUMERIC(12,2) NOT NULL,
    reason               TEXT NOT NULL CHECK (reason IN ('ENROLLED', 'UPGRADE', 'DOWNGRADE')),
    trigger_ref          TEXT,
    CHECK ((reason = 'ENROLLED') = (from_tier_id IS NULL))
);

CREATE TABLE exceptions (
    exception_id TEXT PRIMARY KEY,
    code         TEXT NOT NULL CHECK (code IN ('NEGATIVE_BALANCE_AFTER_RETURN')),
    account_id   TEXT NOT NULL REFERENCES loyalty_accounts (account_id) DEFERRABLE INITIALLY DEFERRED,
    ref_id       TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    detail       TEXT NOT NULL
);

-- Every foreign-key column is indexed (unless a PK/UNIQUE already covers it).
-- Besides speeding up joins, SQLite needs these to check foreign keys: without
-- an index on the child column, each parent insert scans the whole child table,
-- which made a full-scale load quadratic.
CREATE INDEX ix_rules_tier ON rules (tier_id);
CREATE INDEX ix_rewards_min_tier ON rewards (min_tier_id);
CREATE INDEX ix_accounts_tier ON loyalty_accounts (current_tier_id);
CREATE INDEX ix_orders_account ON orders (account_id, order_at);
CREATE INDEX ix_orders_tier ON orders (tier_id_at_order);
CREATE INDEX ix_lines_order ON order_lines (order_id);
CREATE INDEX ix_returns_order ON order_returns (order_id);
CREATE INDEX ix_returns_line ON order_returns (line_id);
CREATE INDEX ix_returns_reversal ON order_returns (reversal_txn_id);
CREATE INDEX ix_redemptions_account ON redemptions (account_id);
CREATE INDEX ix_redemptions_reward ON redemptions (reward_id);
CREATE INDEX ix_ledger_account_time ON points_transactions (account_id, created_at);
CREATE INDEX ix_ledger_order ON points_transactions (order_id);
CREATE INDEX ix_ledger_redemption ON points_transactions (redemption_id);
CREATE INDEX ix_ledger_batch ON points_transactions (expiry_batch_id);
CREATE INDEX ix_ledger_reverses ON points_transactions (reverses_txn_id);
CREATE INDEX ix_alloc_lot ON lot_allocations (lot_txn_id);
CREATE INDEX ix_txn_rules_rule ON transaction_rules (rule_id);
CREATE INDEX ix_tier_changes_account ON tier_changes (account_id, changed_at);
CREATE INDEX ix_tier_changes_from ON tier_changes (from_tier_id);
CREATE INDEX ix_tier_changes_to ON tier_changes (to_tier_id);
CREATE INDEX ix_exceptions_account ON exceptions (account_id);
