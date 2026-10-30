"""Command line.

    python -m loyalty.cli generate --scale starter     build data, labels, reports, manifest
    python -m loyalty.cli validate out/starter         re-check the files on disk
    python -m loyalty.cli reproduce --scale starter    build twice, compare checksums

Output layout (labels are kept apart from the data on purpose):
    out/<scale>/README.md                        what is in the folder, how it was made
    out/<scale>/manifest.json                    command, seed, provenance, row counts, SHA-256 per file
    out/<scale>/data/*.csv                       the dataset
    out/<scale>/json/*.json                      same tables as JSON
    out/<scale>/sql/loyalty.db                   SQLite with PK/FK/CHECK constraints enforced
    out/<scale>/sql/loyalty_seed.sql             schema + INSERTs (replayable seed script)
    out/<scale>/sql/schema.sql                   the DDL on its own
    out/<scale>/labels/ground_truth.csv|json     what was planted (answer key)
    out/<scale>/reports/validation_summary.md    every check: PASS / EXPECTED / FAIL
    out/<scale>/reports/validation_results.json
    out/<scale>/reports/findings.csv             what the detector flagged
    out/<scale>/reports/detection_metrics.json   detector scored against labels
"""
import argparse
import json
import shutil
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .anomalies import GroundTruth, default_per_type, inject
from .config import DEFAULT_SEED
from .detect import Finding, detect, score
from .export import (SCHEMA_SQL, read_rows, read_tables, sha256, tables, write_csv,
                     write_json, write_json_tables, write_manifest, write_rows,
                     write_sql_dump, write_sqlite, write_text)
from .generator import SCALES, generate
from .validate import run_validation, summary_markdown

FORMATS = ("csv", "json", "sqlite", "sql")
OUTPUTS = ("data", "json", "sql", "labels", "reports", "manifest.json", "README.md")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="loyalty")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate the synthetic dataset")
    _add_build_args(gen)
    gen.add_argument("--out", type=Path, help="output folder (default out/<scale>)")

    val = sub.add_parser("validate", help="validate a generated folder from its files")
    val.add_argument("folder", type=Path, help="e.g. out/starter")

    rep = sub.add_parser("reproduce", help="generate twice with the same seed and compare checksums")
    _add_build_args(rep)
    rep.add_argument("--out", type=Path, default=Path("out") / "_reproduce")

    args = parser.parse_args(argv)
    if args.command == "generate":
        params = _params(args)
        manifest = build(args.out or Path("out") / args.scale, params)
        return 1 if manifest["validation"]["FAIL"] else 0
    if args.command == "validate":
        return _validate(args.folder)
    return _reproduce(args)


def _add_build_args(p):
    p.add_argument("--scale", choices=SCALES, default="starter")
    p.add_argument("--customers", type=int, help="override customer count")
    p.add_argument("--orders", type=int, help="override order count")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--anomalies-per-type", type=int,
                   help="planted issues per type (default: customers / 200, min 5)")
    p.add_argument("--no-anomalies", action="store_true", help="clean data only")
    p.add_argument("--formats", default=",".join(FORMATS),
                   help=f"comma list of {', '.join(FORMATS)} (default: all)")


def _params(args) -> dict:
    size = dict(SCALES[args.scale])
    size["customers"] = args.customers or size["customers"]
    size["orders"] = args.orders or size["orders"]
    per_type = 0 if args.no_anomalies else (args.anomalies_per_type
                                            or default_per_type(size["customers"]))
    formats = [f for f in args.formats.split(",") if f]
    unknown = set(formats) - set(FORMATS)
    if unknown:
        raise SystemExit(f"unknown format(s): {', '.join(sorted(unknown))}")
    # canonical command (no --out), so two runs into different folders match
    command = f"python -m loyalty.cli generate --scale {args.scale} --seed {args.seed}"
    if args.customers:
        command += f" --customers {args.customers}"
    if args.orders:
        command += f" --orders {args.orders}"
    if args.no_anomalies:
        command += " --no-anomalies"
    elif args.anomalies_per_type:
        command += f" --anomalies-per-type {args.anomalies_per_type}"
    if args.formats != ",".join(FORMATS):
        command += f" --formats {args.formats}"
    return {"command": command, "seed": args.seed, "scale": args.scale, **size,
            "anomalies_per_type": per_type, "formats": formats}


def build(out: Path, params: dict, quiet: bool = False) -> dict:
    say = (lambda *a: None) if quiet else print
    started = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:  # only our own outputs are cleared
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    engine = generate(params["customers"], params["orders"], params["seed"])
    labels = inject(engine, params["seed"], params["anomalies_per_type"]) \
        if params["anomalies_per_type"] else []
    data = tables(engine)
    formats = params["formats"]

    counts = write_csv(engine, out / "data") if "csv" in formats else \
        {name: len(rows) for name, rows in data.items()}
    if "json" in formats:
        write_json_tables(data, out / "json")
    if "sqlite" in formats:
        write_sqlite(data, out / "sql" / "loyalty.db")
    if "sql" in formats:
        write_sql_dump(data, out / "sql" / "loyalty_seed.sql")
    if "sqlite" in formats or "sql" in formats:
        # re-written (not copied) so git's checkout line endings can't change the bytes
        write_text(out / "sql" / "schema.sql", SCHEMA_SQL.read_text(encoding="utf-8"))
    write_rows(labels, out / "labels" / "ground_truth.csv", GroundTruth)
    write_json(labels, out / "labels" / "ground_truth.json")

    say(f"{params['command']}\n  -> {out}")
    for name, n in counts.items():
        say(f"  {name:<22}{n:>10,}")
    say("  points by type:   ", dict(Counter(t.txn_type for t in engine.ledger)))
    say("  redemptions:      ", dict(Counter(r.reject_reason or r.status for r in engine.redemptions)))
    say("  current tiers:    ", dict(Counter(a.current_tier_id for a in engine.accounts.values())))

    statuses = _evaluate(data, labels, out / "reports", say)
    _write_dataset_readme(out, params, counts, len(labels), statuses)
    manifest = write_manifest(out, {k: v for k, v in params.items()}, data, labels)
    manifest["validation"] = statuses
    say(f"  manifest: {len(manifest['files'])} files checksummed -> {out / 'manifest.json'}")
    say(f"done in {time.perf_counter() - started:.1f}s")
    return manifest


def _validate(folder: Path) -> int:
    manifest_path = folder / "manifest.json"
    tampered = []
    if manifest_path.exists():
        files = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
        for rel, meta in files.items():
            if rel.startswith("reports/"):
                continue  # regenerated by this command
            path = folder / rel
            if not path.exists() or sha256(path) != meta["sha256"]:
                tampered.append(rel)
        print(f"checksums: {len(files)} files in manifest, {len(tampered)} changed or missing")
        for rel in tampered:
            print(f"    CHANGED {rel}")
    data = read_tables(folder / "data")
    labels_file = folder / "labels" / "ground_truth.csv"
    labels = read_rows(labels_file, GroundTruth) if labels_file.exists() else []
    print(f"validating {folder} ({sum(len(v) for v in data.values()):,} rows, {len(labels)} labels)")
    statuses = _evaluate(data, labels, folder / "reports", print)
    return 1 if statuses["FAIL"] or tampered else 0


def _reproduce(args) -> int:
    params = _params(args)
    runs = []
    for i in (1, 2):
        print(f"run {i}/2 ...")
        runs.append(build(args.out / f"run_{i}", params, quiet=True))
    a, b = (r["files"] for r in runs)
    print(f"\n{params['command']}\n")
    print(f"  {'file':<42}{'rows / bytes':>14}  run 1 sha256      run 2 sha256      match")
    same = True
    for rel in sorted(set(a) | set(b)):
        ha, hb = a.get(rel, {}).get("sha256", "-"), b.get(rel, {}).get("sha256", "-")
        match = ha == hb
        same &= match
        size = a.get(rel, b.get(rel))["bytes"]
        print(f"  {rel:<42}{size:>14,}  {ha[:16]}  {hb[:16]}  {'yes' if match else 'NO'}")
    rows_a = {k: v["rows"] for k, v in runs[0]["tables"].items()}
    rows_b = {k: v["rows"] for k, v in runs[1]["tables"].items()}
    same &= rows_a == rows_b
    report = {"command": params["command"], "identical": same, "row_counts_match": rows_a == rows_b,
              "files_compared": len(set(a) | set(b)),
              "files": {rel: {"run_1": a.get(rel, {}).get("sha256"), "run_2": b.get(rel, {}).get("sha256")}
                        for rel in sorted(set(a) | set(b))}}
    write_text(args.out / "reproducibility_report.json", json.dumps(report, indent=2))
    print(f"\n{'IDENTICAL' if same else 'DIFFERENT'}: {report['files_compared']} files, "
          f"row counts {'match' if rows_a == rows_b else 'differ'} -> {args.out / 'reproducibility_report.json'}")
    return 0 if same else 1


def _evaluate(data: dict, labels: list, reports: Path, say=print) -> dict:
    """Run validation + detection, write reports, print a summary."""
    reports.mkdir(parents=True, exist_ok=True)
    results = run_validation(data, labels)
    write_text(reports / "validation_summary.md", summary_markdown(results))
    write_text(reports / "validation_results.json",
               json.dumps([asdict(r) for r in results], indent=2, default=str))
    statuses = Counter(r.status for r in results)
    say(f"  validation: {len(results)} checks  PASS {statuses['PASS']}  "
        f"EXPECTED {statuses['EXPECTED']}  FAIL {statuses['FAIL']}")
    for r in results:
        if r.status != "PASS":
            say(f"    {r.check_id} {r.status:<8} {r.violations:>5}  {r.description}")

    findings = detect(data)
    metrics = score(findings, labels)
    write_rows(findings, reports / "findings.csv", Finding)
    write_text(reports / "detection_metrics.json", json.dumps(metrics, indent=2))
    say(f"  detection: {len(labels)} planted, {len(findings)} flagged")
    for issue, m in metrics.items():
        say(f"    {issue:<22} planted {m['planted']:>4}  found {m['true_positive']:>4}  "
            f"false+ {m['false_positive']:>3}  precision {m['precision']:.2f}  recall {m['recall']:.2f}")
    return {"PASS": statuses["PASS"], "EXPECTED": statuses["EXPECTED"], "FAIL": statuses["FAIL"]}


def _write_dataset_readme(out: Path, params: dict, counts: dict, n_labels: int, statuses: dict):
    rows = "\n".join(f"| `{name}` | {n:,} |" for name, n in counts.items())
    text = f"""# Loyalty ledger synthetic dataset ({params['scale']})

Regenerate this exact folder with:

```
{params['command']}
```

Seed `{params['seed']}` - {params['customers']:,} customers, {params['orders']:,} orders,
{params['anomalies_per_type']} planted anomalies per type ({n_labels} labels).
Fully synthetic; no external or personal data. `manifest.json` lists the SHA-256 of every file.

Validation: {statuses['PASS']} PASS, {statuses['EXPECTED']} EXPECTED (planted), {statuses['FAIL']} FAIL
(see `reports/validation_summary.md`).

| Folder | Contents |
|---|---|
| `data/` | one CSV per table (the dataset) |
| `json/` | the same tables as JSON (money as exact decimal strings) |
| `sql/` | `loyalty.db` (SQLite, constraints enforced), `loyalty_seed.sql` (replayable), `schema.sql` |
| `labels/` | `ground_truth.csv/.json` - the planted issues. Never join these into model features. |
| `reports/` | validation summary/results, detector findings and precision/recall |

| Table | Rows |
|---|---:|
{rows}

Field definitions: see `docs/data_dictionary.md` in the repository.
"""
    write_text(out / "README.md", text)


if __name__ == "__main__":
    sys.exit(main())
