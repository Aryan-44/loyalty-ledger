"""Command line: python -m loyalty.cli generate --scale starter

Output layout (labels are kept apart from the data on purpose):
    out/<scale>/data/*.csv                     the dataset
    out/<scale>/labels/ground_truth.csv|json   what was planted (answer key)
    out/<scale>/reports/detection_metrics.json detector scored against labels
    out/<scale>/reports/findings.csv           what the detector flagged
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from .anomalies import GroundTruth, default_per_type, inject
from .config import DEFAULT_SEED
from .detect import Finding, detect, score
from .export import tables, write_csv, write_json, write_rows
from .generator import SCALES, generate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="loyalty")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate the synthetic dataset")
    gen.add_argument("--scale", choices=SCALES, default="starter")
    gen.add_argument("--customers", type=int, help="override customer count")
    gen.add_argument("--orders", type=int, help="override order count")
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--anomalies-per-type", type=int,
                     help="planted issues per type (default: customers / 200, min 5)")
    gen.add_argument("--no-anomalies", action="store_true", help="clean data only")
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

    labels = []
    if not args.no_anomalies:
        per_type = args.anomalies_per_type or default_per_type(size["customers"])
        labels = inject(engine, args.seed, per_type)

    data = tables(engine)
    counts = write_csv(engine, out / "data")
    write_rows(labels, out / "labels" / "ground_truth.csv", GroundTruth)
    write_json(labels, out / "labels" / "ground_truth.json")

    findings = detect(data)
    metrics = score(findings, labels)
    write_rows(findings, out / "reports" / "findings.csv", Finding)
    (out / "reports" / "detection_metrics.json").write_text(json.dumps(metrics, indent=2))
    elapsed = time.perf_counter() - started

    print(f"seed={args.seed} customers={size['customers']} orders={size['orders']} "
          f"-> {out}  ({elapsed:.1f}s)")
    for name, n in counts.items():
        print(f"  {name:<22}{n:>10,}")
    print("  points by type:   ", dict(Counter(t.txn_type for t in engine.ledger)))
    print("  redemptions:      ", dict(Counter(r.reject_reason or r.status for r in engine.redemptions)))
    print("  current tiers:    ", dict(Counter(a.current_tier_id for a in engine.accounts.values())))
    print(f"  planted anomalies: {len(labels)}   detector findings: {len(findings)}")
    for issue, m in metrics.items():
        print(f"    {issue:<22} planted {m['planted']:>4}  found {m['true_positive']:>4}  "
              f"false+ {m['false_positive']:>3}  precision {m['precision']:.2f}  recall {m['recall']:.2f}")


if __name__ == "__main__":
    main()
