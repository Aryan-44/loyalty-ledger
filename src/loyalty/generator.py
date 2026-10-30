"""Synthetic data generator ("Part A").

Builds a seeded timeline of customer behaviour - sign-ups, orders, returns,
redemption attempts and monthly jobs - and replays it, in time order, through
the rules engine ("Part B"). The generator decides only *what customers do*;
every outcome (points, balances, tiers, approvals, expiries) is produced by the
engine's rules, never assigned at random.

Same seed + same scale => identical output.
"""
import heapq
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .engine import LineItem, LoyaltyEngine
from .ids import customer_id
from .models import Customer

SCALES = {
    "tiny": {"customers": 200, "orders": 1_000},
    "starter": {"customers": 2_000, "orders": 10_000},
    "full": {"customers": 20_000, "orders": 100_000},
}

SIM_START = datetime(2026, 1, 1)
SIM_END = datetime(2028, 1, 1)  # two years: first-year points expire during year two

# category: (share of basket lines, min price, max price, GST rate, return rate)
CATEGORIES = {
    "Electronics": (0.10, 999, 60_000, Decimal("0.18"), 0.05),
    "Apparel":     (0.24, 299, 4_999, Decimal("0.12"), 0.15),
    "Footwear":    (0.12, 699, 7_999, Decimal("0.12"), 0.10),
    "Home":        (0.15, 199, 9_999, Decimal("0.18"), 0.04),
    "Beauty":      (0.15, 149, 2_999, Decimal("0.18"), 0.03),
    "Grocery":     (0.20, 49, 1_499, Decimal("0.05"), 0.00),
    "Gift Cards":  (0.04, 500, 10_000, Decimal("0"), 0.00),
}
SKUS_PER_CATEGORY = 40

# segment: (share of customers, relative order frequency)
SEGMENTS = {"OCCASIONAL": (0.60, 1.0), "REGULAR": (0.30, 3.0), "VIP": (0.10, 10.0)}
# redeemer style: (share of customers, redemption attempts per order)
REDEEMER_STYLES = {"HOARDER": (0.25, 0.2), "NORMAL": (0.55, 1.4), "EAGER": (0.20, 2.8)}

DOUBLE_CLICK_RATE = 0.02   # two requests, same second, different keys
RETRY_RATE = 0.01          # same request key sent twice
ONLINE_SHARE = 0.65
FREE_SHIPPING_FROM = Decimal("499")
SHIPPING_FEE = Decimal("49")

FIRST_NAMES = ["Aarav", "Asha", "Vikram", "Priya", "Rahul", "Sneha", "Arjun", "Kavya",
               "Rohan", "Isha", "Karan", "Meera", "Aditya", "Divya", "Nikhil", "Pooja",
               "Siddharth", "Ananya", "Varun", "Neha", "Manish", "Riya", "Sanjay", "Tara",
               "Imran", "Fatima", "John", "Grace", "Harpreet", "Simran"]
LAST_NAMES = ["Sharma", "Rao", "Patel", "Iyer", "Reddy", "Nair", "Gupta", "Singh",
              "Kumar", "Das", "Menon", "Joshi", "Khan", "Fernandes", "Mehta", "Bose",
              "Chopra", "Pillai", "Verma", "Kulkarni"]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Pune", "Kolkata",
          "Ahmedabad", "Jaipur", "Kochi", "Lucknow", "Indore"]

# event priority when timestamps tie: jobs first, then sign-ups, bonuses, orders, returns, redemptions
P_MONTHLY, P_ENROLL, P_BONUS, P_ORDER, P_RETURN, P_REDEEM = range(6)


@dataclass(order=True)
class Event:
    at: datetime
    priority: int
    seq: int
    kind: str = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)


def generate(customers: int, orders: int, seed: int) -> LoyaltyEngine:
    """Build the timeline and replay it through a fresh engine."""
    rng = random.Random(seed)
    catalog = _build_catalog(rng)
    events: list[Event] = []
    seq = iter(range(10**9))

    # ---- customers and sign-ups
    profiles = []
    for n in range(1, customers + 1):
        cid = customer_id(n)
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        signup = _signup_time(rng)
        segment = _pick(rng, SEGMENTS)
        style = _pick(rng, REDEEMER_STYLES)
        birth = datetime(rng.randint(1956, 2008), 1, 1) + timedelta(days=rng.randint(0, 364))
        customer = Customer(cid, first, last, f"{first}.{last}.{n}@example.com".lower(),
                            rng.choice(CITIES), signup, birth)
        events.append(Event(signup, P_ENROLL, next(seq), "ENROLL", {"customer": customer}))
        for year in (SIM_START.year, SIM_START.year + 1):
            birthday = _birthday_in(birth, year)
            if signup < birthday < SIM_END:
                events.append(Event(birthday, P_BONUS, next(seq), "BIRTHDAY",
                                    {"account": f"LOY-{cid}"}))
        weight = SEGMENTS[segment][1] * rng.lognormvariate(0, 0.5)
        profiles.append({"cid": cid, "signup": signup, "weight": weight,
                         "redeem_rate": REDEEMER_STYLES[style][1]})

    # ---- orders: distribute exactly `orders` across customers by weight
    owners = rng.choices(profiles, weights=[p["weight"] for p in profiles], k=orders)
    for order_no, owner in enumerate(owners):
        at = _order_time(rng, owner["signup"])
        items = _basket(rng, catalog)
        online = rng.random() < ONLINE_SHARE
        key = f"order-{order_no}"
        events.append(Event(at, P_ORDER, next(seq), "ORDER", {
            "key": key, "account": f"LOY-{owner['cid']}", "items": items, "online": online}))

        # returns: decided per line from category return rates
        for line_no, item in enumerate(items, start=1):
            if rng.random() < CATEGORIES[item.category][4]:
                qty = item.quantity
                if qty > 1 and rng.random() < 0.4:
                    qty = rng.randint(1, qty - 1)  # partial return
                ret_at = (at + timedelta(days=rng.uniform(1, 30))).replace(microsecond=0)
                if ret_at < SIM_END:
                    events.append(Event(ret_at, P_RETURN, next(seq), "RETURN", {
                        "order_key": key, "line_no": line_no, "qty": qty}))

        # redemption attempts some days after shopping
        attempts = _poisson(rng, owner["redeem_rate"])
        for _ in range(attempts):
            red_at = at + timedelta(days=rng.uniform(1, 60))
            if red_at >= SIM_END:
                continue
            r = rng.random()
            mode = "double_click" if r < DOUBLE_CLICK_RATE else (
                "retry" if r < DOUBLE_CLICK_RATE + RETRY_RATE else "single")
            events.append(Event(red_at.replace(microsecond=0), P_REDEEM, next(seq), "REDEEM", {
                "account": f"LOY-{owner['cid']}", "u_intent": rng.random(),
                "u_pick": rng.random(), "mode": mode}))

    # ---- monthly jobs: expiry run + tier review on the 1st of each month
    month = datetime(SIM_START.year, SIM_START.month + 1, 1)
    while month <= SIM_END:
        events.append(Event(month, P_MONTHLY, next(seq), "MONTHLY"))
        month = datetime(month.year + month.month // 12, month.month % 12 + 1, 1)

    return _replay(events)


def _replay(events: list[Event]) -> LoyaltyEngine:
    engine = LoyaltyEngine()
    order_ids: dict[str, str] = {}
    rewards = sorted(engine.rewards.values(), key=lambda r: r.points_cost)
    heapq.heapify(events)
    while events:
        ev = heapq.heappop(events)
        d = ev.data
        if ev.kind == "ENROLL":
            acc = engine.enroll(d["customer"], ev.at)
            engine.award_bonus(acc.account_id, ev.at, "ENROLLMENT_BONUS")
        elif ev.kind == "BIRTHDAY":
            engine.award_bonus(d["account"], ev.at, "BIRTHDAY_BONUS")
        elif ev.kind == "ORDER":
            merchandise = sum(i.unit_price * i.quantity - i.discount for i in d["items"])
            tax = sum((i.unit_price * i.quantity - i.discount) * CATEGORIES[i.category][3]
                      for i in d["items"]).quantize(Decimal("0.01"))
            shipping = SHIPPING_FEE if d["online"] and merchandise < FREE_SHIPPING_FROM else Decimal("0")
            order = engine.place_order(d["account"], ev.at, d["items"], tax=tax, shipping=shipping,
                                       channel="ONLINE" if d["online"] else "STORE")
            order_ids[d["key"]] = order.order_id
        elif ev.kind == "RETURN":
            engine.return_items(order_ids[d["order_key"]], d["line_no"], d["qty"], ev.at)
        elif ev.kind == "REDEEM":
            _redeem(engine, rewards, ev)
        elif ev.kind == "MONTHLY":
            engine.run_expiry(ev.at)
            engine.review_tiers(ev.at)
    return engine


def _redeem(engine: LoyaltyEngine, rewards, ev: Event) -> None:
    """Customer looks at their points and picks a reward.

    Most of the time they choose something they can afford (as an app would show);
    sometimes they try a reward they can't have - those attempts are rejected by the
    engine's rules, which is the realistic failure path.
    """
    d = ev.data
    acc = engine.accounts[d["account"]]
    available = engine.available_points(acc.account_id, ev.at)
    rank = engine.tier_by_id[acc.current_tier_id].rank
    affordable = [r for r in rewards if r.points_cost <= available
                  and (r.min_tier_id is None or engine.tier_by_id[r.min_tier_id].rank <= rank)]
    if d["u_intent"] < 0.92:
        if not affordable:
            return  # nothing to spend on - customer doesn't try
        # squaring the uniform draw favours cheaper rewards (list is sorted by cost)
        reward = affordable[int(d["u_pick"] ** 2 * len(affordable))]
    else:
        reward = rewards[int(d["u_pick"] * len(rewards))]

    key = f"req-{ev.seq}"
    engine.redeem(acc.account_id, reward.reward_id, ev.at, request_key=key)
    if d["mode"] == "double_click":
        engine.redeem(acc.account_id, reward.reward_id, ev.at, request_key=f"{key}-b")
    elif d["mode"] == "retry":
        engine.redeem(acc.account_id, reward.reward_id, ev.at, request_key=key)


# ---------------------------------------------------------------- helpers

def _build_catalog(rng: random.Random) -> dict[str, list[tuple[str, Decimal]]]:
    catalog = {}
    for category, (_, lo, hi, _, _) in CATEGORIES.items():
        code = category.upper().replace(" ", "")[:4]
        skus = []
        for i in range(1, SKUS_PER_CATEGORY + 1):
            # log-uniform: many cheap products, few expensive ones
            price = lo * (hi / lo) ** rng.random()
            skus.append((f"SKU-{code}-{i:04d}", _retail_price(price)))
        catalog[category] = skus
    return catalog


def _retail_price(x: float) -> Decimal:
    """Round to a shelf price like 1,299 or 449."""
    if x < 100:
        return Decimal(round(x))
    return Decimal(max(99, round(x / 50) * 50 - 1))


def _basket(rng: random.Random, catalog) -> list[LineItem]:
    names = list(CATEGORIES)
    shares = [CATEGORIES[c][0] for c in names]
    items = []
    for _ in range(rng.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]):
        category = rng.choices(names, weights=shares)[0]
        sku, price = rng.choice(catalog[category])
        qty = rng.choices([1, 2, 3], weights=[80, 15, 5])[0]
        discount = Decimal("0")
        if category != "Gift Cards" and rng.random() < 0.2:
            discount = (price * qty * Decimal(rng.randint(5, 30)) / 100).quantize(Decimal("0.01"))
        items.append(LineItem(sku, category, qty, price, discount))
    return items


def _signup_time(rng: random.Random) -> datetime:
    """Most members join early; a long tail joins later (capped at 18 months)."""
    days = min(rng.expovariate(1 / 120), 540)
    return (SIM_START + timedelta(days=days)).replace(microsecond=0)


def _birthday_in(birth: datetime, year: int) -> datetime:
    day = 28 if (birth.month, birth.day) == (2, 29) else birth.day
    return datetime(year, birth.month, day, 9, 0)


def _season(at: datetime) -> float:
    weight = 1.0
    if datetime(at.year, 10, 15) <= at < datetime(at.year, 11, 15):
        weight = 1.8   # festive sale
    elif at.month == 12:
        weight = 1.3
    if at.weekday() >= 5:
        weight *= 1.2
    return weight


def _order_time(rng: random.Random, signup: datetime) -> datetime:
    span = (SIM_END - signup).total_seconds()
    while True:  # rejection sampling gives the seasonal shape
        day = signup + timedelta(seconds=rng.random() * span)
        if rng.random() * 2.16 < _season(day):
            break
    at = day.replace(hour=rng.randint(8, 21), minute=rng.randint(0, 59),
                     second=rng.randint(0, 59), microsecond=0)
    return max(at, signup + timedelta(minutes=1))


def _pick(rng: random.Random, table: dict) -> str:
    names = list(table)
    return rng.choices(names, weights=[table[n][0] for n in names])[0]


def _poisson(rng: random.Random, mean: float) -> int:
    # Knuth's method - fine for small means
    limit, k, p = pow(2.718281828459045, -mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
