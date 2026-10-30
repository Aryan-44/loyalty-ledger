# Worked example: one customer, six events

Calculated by hand before writing code. `tests/test_engine_example.py` replays it
through the engine and checks every number.

Rules used (from `src/loyalty/config.py`): Bronze earns 10 pts per INR 100,
Silver needs INR 20,000 qualifying spend in 365 days, points expire after 365
days, redemptions use oldest points first, tax and shipping never earn points.

## Events

| # | When | Event | Detail |
|---|---|---|---|
| 1 | 2026-01-05 11:00 | Purchase | Shoes 2,000 + Socks 200, tax 220, shipping 50 |
| 2 | 2026-02-10 15:30 | Purchase | Laptop 18,000 + Jacket 3,000, tax 2,100 |
| 3 | 2026-02-20 12:00 | Return | Jacket (line 2 of order 2) |
| 4 | 2026-03-01 10:00:00 | Redeem | RWD-001, 1,500 pts |
| 5 | 2026-03-01 10:00:00 | Redeem | RWD-001 again, same second |
| 6 | 2027-02-15 00:00 | Expiry run | |

## Reasoning

1. Qualifying 2,200 -> 220 pts at Bronze. Lot A = 220, expires 2027-01-05.
2. Qualifying 21,000 -> 2,100 pts, still Bronze rate (tier held when buying).
   Lot B = 2,100, expires 2027-02-10. Spend 23,200 -> upgrade to Silver.
3. Jacket is 3,000 of 21,000 -> reverse 300 pts, taken from Lot B (now 1,800).
   Reversal points to the original accrual. Spend 20,200 -> stays Silver.
4. Available 2,020 >= 1,500 -> approved. Lot A 220 -> 0, Lot B 1,800 -> 520.
5. Available 520 < 1,500 -> rejected (INSUFFICIENT_BALANCE). No ledger row.
6. Lot A expired with 0 left (nothing to do). Lot B expired with 520 -> -520.
   Running expiry again adds nothing.

## Resulting ledger

| txn_id | type | points | balance_after | reverses |
|---|---|---|---|---|
| PTS-2026-00000001 | ACCRUAL | +220 | 220 | |
| PTS-2026-00000002 | ACCRUAL | +2100 | 2320 | |
| PTS-2026-00000003 | REVERSAL | -300 | 2020 | PTS-2026-00000002 |
| PTS-2026-00000004 | REDEMPTION | -1500 | 520 | |
| PTS-2027-00000001 | EXPIRY | -520 | 0 | |
