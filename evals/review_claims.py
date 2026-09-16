"""Export a complete paragraph review packet; never invent human annotations."""
import argparse
import hashlib
import json
from pathlib import Path

from evals.audit import atomic_json


def export_review(results: dict) -> list[dict]:
    packet = []
    for case in results["cases"]:
        final = case.get("final") or {}
        for index, paragraph in enumerate(final.get("answer_markdown", "").split("\n\n")):
            if "[Source " in paragraph:
                packet.append({"id": f"{case['id']}:{index}", "text": paragraph,
                               "citations": final.get("citations", []), "label": None, "reviewer": None})
    return packet


def summarize_review(original: list[dict], reviewed: list[dict]) -> dict:
    if len(original) != len(reviewed) or {r["id"] for r in original} != {r["id"] for r in reviewed}:
        raise ValueError("Review must cover the complete original packet")
    records = {row["id"]: row for row in original}
    for row in reviewed:
        if row.get("label") not in {"supported", "unsupported", "contradicted"} or not row.get("reviewer"):
            raise ValueError("Every paragraph needs a human reviewer and support label")
        if any(row[field] != records[row["id"]][field] for field in ("text", "citations")):
            raise ValueError("Evidence or answer was modified during review")
    return {"reviewed_paragraphs": len(reviewed), "paragraph_support_rate":
            sum(row["label"] == "supported" for row in reviewed) / len(reviewed) if reviewed else None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--reviewed", type=Path)
    args = parser.parse_args()
    data = args.results.read_bytes()
    packet = export_review(json.loads(data))
    if args.reviewed:
        reviewed = json.loads(args.reviewed.read_text())
        if reviewed["results_sha256"] != hashlib.sha256(data).hexdigest():
            raise ValueError("Review belongs to different results")
        print(json.dumps(summarize_review(packet, reviewed["paragraphs"]), indent=2))
    else:
        atomic_json(args.results.with_name("claim-review.json"), {
            "results_sha256": hashlib.sha256(data).hexdigest(), "paragraphs": packet})


if __name__ == "__main__":
    main()
