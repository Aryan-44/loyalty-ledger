"""Program configuration: tiers, earning rules and rewards.

All business numbers live here as data. The engine reads them; it never
hard-codes a threshold or rate. Changing a rule = editing this file.
"""
from datetime import datetime
from decimal import Decimal

from .models import Reward, Rule, Tier

PROGRAM_START = datetime(2026, 1, 1)
DEFAULT_SEED = 20261022

TIERS = [
    Tier("TIER-BRONZE", "Bronze", 1, Decimal("0"), PROGRAM_START),
    Tier("TIER-SILVER", "Silver", 2, Decimal("20000"), PROGRAM_START),
    Tier("TIER-GOLD", "Gold", 3, Decimal("50000"), PROGRAM_START),
    Tier("TIER-PLATINUM", "Platinum", 4, Decimal("100000"), PROGRAM_START),
]

# rule_type values:
#   EARN_RATE           points per INR 100 of qualifying spend, per tier
#   CATEGORY_EXCLUSION  lines in this category earn nothing and don't count to tier
#   CATEGORY_MULTIPLIER points for this category multiplied while the rule is effective
#   POINTS_EXPIRY       days an accrual lot stays valid
#   TIER_WINDOW         rolling window (days) of qualifying spend used for tier
#   ENROLLMENT_BONUS    points granted once when a member joins
#   BIRTHDAY_BONUS      points granted on the member's birthday each year
RULES = [
    Rule("RULE-001", "EARN_RATE", "Bronze earns 10 pts per INR 100", Decimal("10"),
         PROGRAM_START, tier_id="TIER-BRONZE"),
    Rule("RULE-002", "EARN_RATE", "Silver earns 15 pts per INR 100", Decimal("15"),
         PROGRAM_START, tier_id="TIER-SILVER"),
    Rule("RULE-003", "EARN_RATE", "Gold earns 20 pts per INR 100", Decimal("20"),
         PROGRAM_START, tier_id="TIER-GOLD"),
    Rule("RULE-004", "EARN_RATE", "Platinum earns 25 pts per INR 100", Decimal("25"),
         PROGRAM_START, tier_id="TIER-PLATINUM"),
    Rule("RULE-005", "CATEGORY_EXCLUSION", "Gift cards earn no points", Decimal("0"),
         PROGRAM_START, category="Gift Cards"),
    Rule("RULE-006", "CATEGORY_MULTIPLIER", "Festive sale: double points on Electronics",
         Decimal("2"), datetime(2026, 10, 15), effective_to=datetime(2026, 11, 15),
         category="Electronics"),
    Rule("RULE-007", "POINTS_EXPIRY", "Points expire 365 days after they are earned",
         Decimal("365"), PROGRAM_START),
    Rule("RULE-008", "TIER_WINDOW", "Tier uses qualifying spend of the last 365 days",
         Decimal("365"), PROGRAM_START),
    Rule("RULE-009", "ENROLLMENT_BONUS", "Welcome bonus when joining", Decimal("200"),
         PROGRAM_START),
    Rule("RULE-010", "BIRTHDAY_BONUS", "Bonus on the member's birthday", Decimal("300"),
         PROGRAM_START),
]

REWARDS = [
    Reward("RWD-001", "INR 150 voucher", 1500),
    Reward("RWD-002", "INR 50 voucher", 500),
    Reward("RWD-003", "Free delivery", 300),
    Reward("RWD-004", "INR 500 voucher", 5000),
    Reward("RWD-005", "Branded coffee mug", 800, min_tier_id="TIER-SILVER"),
    Reward("RWD-006", "Airport lounge pass", 8000, min_tier_id="TIER-GOLD"),
    Reward("RWD-007", "INR 10 off next order", 100),
    Reward("RWD-008", "Free coffee", 200),
]
