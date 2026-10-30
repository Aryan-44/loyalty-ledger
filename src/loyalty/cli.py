"""Command line: python -m loyalty.cli generate --scale starter"""
import argparse
import time
from collections import Counter
from pathlib import Path

from .config import DEFAULT_SEED
from .export import write_csv
from .generator import SCALES, generate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="loyalty")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate the synthetic dataset")
    gen.add_argument("--scale", choices=SCALES, default="starter")
    gen.add_argument("--customers", type=int, help="override customer count")
    gen.add_argument("--orders", type=int, help="override order count")
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--out", type=Path, help="output folder (default out/<scale>)")
    args = parser.parse_args(argv)

    size = dict(SCALES[args.scale])
    if args.customers:
        size["customers"] = args.customers
    if args.orders:
        size["orders"] = args.orders
    out = args.out or Path("out") / args.scale

    started = time.perf_counter()
    engine = generate(size["customers"], size["orders"], args.seed)
    counts = write_csv(engine, out)
    elapsed = time.perf_counter() - started

    print(f"seed={args.seed} customers={size['customers']} orders={size['orders']} "
          f"-> {out}  ({elapsed:.1f}s)")
    for name, n in counts.items():
        print(f"  {name:<22}{n:>10,}")
    print("  points by type:   ", dict(Counter(t.txn_type for t in engine.ledger)))
    print("  redemptions:      ", dict(Counter(r.reject_reason or r.status for r in engine.redemptions)))
    print("  current tiers:    ", dict(Counter(a.current_tier_id for a in engine.accounts.values())))


if __name__ == "__main__":
    main()
