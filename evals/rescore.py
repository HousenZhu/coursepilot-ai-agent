"""Rescore immutable observations; never rerun the model or overwrite an original run."""
import argparse
import hashlib
import json
from pathlib import Path

from evals.audit import atomic_json, snapshot_source
from evals.dataset import dataset_sha256
from evals.run import ROOT, build_report
from evals.scoring import aggregate_results, score_case


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    results_bytes = (args.run / "results.json").read_bytes()
    manifest_bytes = (args.run / "manifest.json").read_bytes()
    source_manifest = json.loads(manifest_bytes)
    results = json.loads(results_bytes)["cases"]
    cases = json.loads((args.run / "dataset.json").read_bytes())
    if not source_manifest.get("complete") or not source_manifest.get("source_unchanged"):
        raise ValueError("Rescoring requires complete, unchanged-source observations")
    if dataset_sha256(cases) != source_manifest["dataset_sha256"]:
        raise ValueError("Source dataset hash mismatch")
    observations = {result["id"]: result for result in results}
    if len(observations) != len(results) or set(observations) != {case["id"] for case in cases}:
        raise ValueError("Every original case must be retained exactly once")
    if any("http_status" not in result or "visible_events" not in result for result in results):
        raise ValueError("Original observations lack required transport or streamed-event fields")
    args.output.mkdir(parents=True, exist_ok=False)
    rescored = [score_case(case, observations[case["id"]]) for case in cases]
    metrics = aggregate_results(rescored)
    manifest = {
        **source_manifest,
        "run_id": args.output.name,
        "evaluation_kind": "rescore_only_no_new_model_calls",
        "derived_from": source_manifest["run_id"],
        "rescore_reason": args.reason,
        "original_results_sha256": hashlib.sha256(results_bytes).hexdigest(),
        "original_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "rescore_source_sha256": snapshot_source(ROOT, args.output / "scorer-source.zip"),
        "scorer_sha256": hashlib.sha256(Path(__file__).with_name("scoring.py").read_bytes()).hexdigest(),
    }
    changes = [{"id": row["id"], "changed_assertions": {
        key: {"before": observations[row["id"]].get(key), "after": row[key]}
        for key in ("task_success", "canary_leak", "authorization_pass", "grounding_pass", "routing_pass")
        if row[key] != observations[row["id"]].get(key)
    }} for row in rescored]
    atomic_json(args.output / "dataset.json", cases)
    atomic_json(args.output / "results.json", {"metrics": metrics, "cases": rescored})
    atomic_json(args.output / "manifest.json", manifest)
    atomic_json(args.output / "assertion-changes.json", [row for row in changes if row["changed_assertions"]])
    report = build_report(args.output.name, manifest["dataset_role"], metrics, manifest)
    report += f"\n## Rescoring Audit\n\nOriginal run: `{manifest['derived_from']}`.\n\n"
    report += "No new model requests. All original cases, responses, labels and timings were retained.\n\n"
    report += args.reason + "\n"
    (args.output / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
