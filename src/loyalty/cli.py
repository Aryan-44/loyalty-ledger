"""Command line.

    python -m loyalty.cli generate --scale starter     build data, labels and reports
    python -m loyalty.cli validate out/starter         re-check the files on disk

Output layout (labels are kept apart from the data on purpose):
    out/<scale>/data/*.csv                       the dataset
    out/<scale>/labels/ground_truth.csv|json     what was planted (answer key)
    out/<scale>/reports/validation_summary.md    every check: PASS / EXPECTED / FAIL
    out/<scale>/reports/validation_results.json
    out/<scale>/reports/findings.csv             what the detector flagged
    out/<scale>/reports/detection_metrics.json   detector scored against labels
"""
import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .anomalies import GroundTruth, default_per_type, inject
from .config import DEFAULT_SEED
from .detect import Finding, detect, score
from .export import read_rows, read_tables, tables, write_csv, write_json, write_rows
from .generator import SCALES, generate
from .validate import run_validation, summary_markdown


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

    val = sub.add_parser("validate", help="validate a generated folder from its CSV files")
    val.add_argument("folder", type=Path, help="e.g. out/starter")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate(args)
    return _validate(args.folder)


def _generate(args) -> int:
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

    counts = write_csv(engine, out / "data")
    write_rows(labels, out / "labels" / "ground_truth.csv", GroundTruth)
    write_json(labels, out / "labels" / "ground_truth.json")
    print(f"seed={args.seed} customers={size['customers']} orders={size['orders']} -> {out}")
    for name, n in counts.items():
        print(f"  {name:<22}{n:>10,}")
    print("  points by type:   ", dict(Counter(t.txn_type for t in engine.ledger)))
    print("  redemptions:      ", dict(Counter(r.reject_reason or r.status for r in engine.redemptions)))
    print("  current tiers:    ", dict(Counter(a.current_tier_id for a in engine.accounts.values())))

    failed = _evaluate(tables(engine), labels, out / "reports")
    print(f"done in {time.perf_counter() - started:.1f}s")
    return 1 if failed else 0


def _validate(folder: Path) -> int:
    data = read_tables(folder / "data")
    labels_file = folder / "labels" / "ground_truth.csv"
    labels = read_rows(labels_file, GroundTruth) if labels_file.exists() else []
    print(f"validating {folder} ({sum(len(v) for v in data.values()):,} rows, {len(labels)} labels)")
    return 1 if _evaluate(data, labels, folder / "reports") else 0


def _evaluate(data: dict, labels: list, reports: Path) -> int:
    """Run validation + detection, write reports, print a summary. Returns #FAIL."""
    reports.mkdir(parents=True, exist_ok=True)
    results = run_validation(data, labels)
    (reports / "validation_summary.md").write_text(summary_markdown(results), encoding="utf-8")
    (reports / "validation_results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2, default=str), encoding="utf-8")
    statuses = Counter(r.status for r in results)
    print(f"  validation: {len(results)} checks  PASS {statuses['PASS']}  "
          f"EXPECTED {statuses['EXPECTED']}  FAIL {statuses['FAIL']}")
    for r in results:
        if r.status != "PASS":
            print(f"    {r.check_id} {r.status:<8} {r.violations:>5}  {r.description}")

    findings = detect(data)
    metrics = score(findings, labels)
    write_rows(findings, reports / "findings.csv", Finding)
    (reports / "detection_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"  detection: {len(labels)} planted, {len(findings)} flagged")
    for issue, m in metrics.items():
        print(f"    {issue:<22} planted {m['planted']:>4}  found {m['true_positive']:>4}  "
              f"false+ {m['false_positive']:>3}  precision {m['precision']:.2f}  recall {m['recall']:.2f}")
    return statuses["FAIL"]


if __name__ == "__main__":
    sys.exit(main())
