"""Descriptive per-task/config reports; never pool backend or synthetic trials."""
import csv
import io
from pathlib import Path
import statistics

from .telemetry import Journal, TOKEN_FIELDS, digest, encoded, valid_record


def aggregate(records):
    """Latest completed attempt per exact identity, with explicit retry counts."""
    records = list(records)
    if any(not valid_record(record) for record in records):
        raise ValueError("cannot aggregate invalid trial records")
    unique = {digest(record["identity"]): record for record in records}
    groups = {}
    for record in unique.values():
        identity = record["identity"]
        key = (identity["task"], identity["condition"], identity["config_hash"],
               identity["source_hash"], record["synthetic"])
        groups.setdefault(key, []).append(record)
    rows = []
    for key, samples in sorted(groups.items()):
        successful = [record for record in samples if record["validator"]["ok"]]
        durations = [record["telemetry"]["wall_seconds"] for record in successful
                     if isinstance(record.get("telemetry", {}).get("wall_seconds"), (int, float))]
        rows.append(dict(task=key[0], condition=key[1], config_hash=key[2], source_hash=key[3],
                         synthetic=key[4], n=len(samples), passed=len(successful),
                         failed=len(samples) - len(successful), wall_n=len(durations),
                         wall_min=min(durations) if durations else None,
                         wall_max=max(durations) if durations else None,
                         wall_median=statistics.median(durations) if durations else None,
                         comparison_eligible=len(successful) == len(samples) and len(samples) >= 3
                         and not key[4]
                         and all(record.get("comparison_eligible") is True for record in samples)))
        for metric in TOKEN_FIELDS:
            values = [record.get("telemetry", {}).get("tokens", {}).get(metric) for record in successful]
            values = [value for value in values if type(value) is int and value >= 0]
            rows[-1].update({f"{metric}_n": len(values),
                             f"{metric}_min": min(values) if values else None,
                             f"{metric}_median": statistics.median(values) if values else None,
                             f"{metric}_max": max(values) if values else None})
    return {"completed_attempts": len(records), "unique_trials": len(unique),
            "superseded_attempts": len(records) - len(unique), "rows": rows}


def write_report(journal_path, output_root):
    records, errors = Journal(journal_path).read()
    summary = aggregate(records)
    summary["journal_errors"] = errors
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    fields = list(summary["rows"][0]) if summary["rows"] else ["task", "n", "passed", "failed"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(summary["rows"])
    (root / "summary.csv").write_text(buffer.getvalue(), encoding="utf-8")
    raw = io.StringIO(newline="")
    raw_writer = csv.DictWriter(raw, fieldnames=["identity", "synthetic", "validator", "telemetry"])
    raw_writer.writeheader()
    for record in {digest(row["identity"]): row for row in records}.values():
        raw_writer.writerow({key: encoded(record.get(key)).decode("utf-8")
                             for key in raw_writer.fieldnames})
    (root / "samples.csv").write_text(raw.getvalue(), encoding="utf-8")
    lines = ["# Token-efficiency descriptive report", "",
             "SYNTHETIC trials are orchestration tests, not model benchmark results.",
             "Correctness first: durations below include successful trials only; failures are not wins.",
             "No significance claims. Medians with N < 3 are descriptive only, not comparisons.",
             "A/C changes backend and docs. A-D are enhanced-code ablations, not the original baseline.",
             "Artifacts/validators are rechecked by resume/validate, not by this historical report.", "",
             f"Completed attempts: {summary['completed_attempts']}; unique trials: {summary['unique_trials']}; "
             f"superseded: {summary['superseded_attempts']}; corrupt/incomplete lines: {len(errors)}.", "",
             "| Task | Condition | Config | Synthetic | N | Pass | Fail | Wall N | Min | Median | Max |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in summary["rows"]:
        lines.append("| " + " | ".join(str(row[key]) for key in (
            "task", "condition", "config_hash", "synthetic", "n", "passed", "failed",
            "wall_n", "wall_min", "wall_median", "wall_max")) + " |")
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
