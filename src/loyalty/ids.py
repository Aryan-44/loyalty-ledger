"""Deterministic, zero-padded IDs: ORD-2026-000001, PTS-2026-00000001, ...

Counters are per (prefix, year), so the same inputs always give the same IDs.
"""
from collections import defaultdict

WIDTHS = {"PTS": 8}
DEFAULT_WIDTH = 6


def customer_id(n: int) -> str:
    return f"C{n:06d}"


def account_id(customer_id: str) -> str:
    return f"LOY-{customer_id}"


class IdFactory:
    def __init__(self):
        self._counters = defaultdict(int)

    def next(self, prefix: str, year: int) -> str:
        self._counters[(prefix, year)] += 1
        width = WIDTHS.get(prefix, DEFAULT_WIDTH)
        return f"{prefix}-{year}-{self._counters[(prefix, year)]:0{width}d}"
