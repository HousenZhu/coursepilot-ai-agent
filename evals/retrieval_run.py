"""Paired page-level retrieval ablation, independent of LLM routing/generation."""
import asyncio
import json
import os
import time
from pathlib import Path
from statistics import mean

from app.config import get_settings
from app.rag.retrieval import search_course_materials
from evals.audit import atomic_json, fixture_snapshot
from evals.dataset import dataset_sha256, expand_templates, load_templates
from evals.scoring import retrieval_metrics


async def main() -> None:
    root = Path(__file__).parent
    cases = [case for case in expand_templates(load_templates(root / "templates.jsonl"), "heldout")
             if case.get("citation_gold")]
    fixture = await fixture_snapshot()
    settings = get_settings()
    original = settings.retrieval_mode
    results = []
    try:
        # All variants and all failures stay in each arm's denominator.
        for mode in ("vector", "hybrid"):
            settings.retrieval_mode = mode
            for case in cases:
                start = time.perf_counter()
                try:
                    retrieved = await search_course_materials(os.environ["EVAL_USER_ID"], case["prompt"],
                        os.environ["EVAL_COURSE_ID"], 6)
                    error = None
                except Exception as exc:
                    retrieved, error = [], type(exc).__name__
                gold = [f"{item['content_id']}:{item['page']}" for item in case["citation_gold"]]
                observed = [f"{item['content_id']}:{item['page']}" for item in retrieved]
                results.append({"id": case["id"], "mode": mode, **retrieval_metrics(gold, observed),
                                "latency_seconds": time.perf_counter() - start, "error": error,
                                "retrieved": observed, "gold": gold})
    finally:
        settings.retrieval_mode = original
    summary = {mode: {key: mean(r[key] for r in results if r["mode"] == mode)
                      for key in ("recall_at_6", "mrr", "latency_seconds")}
               for mode in ("vector", "hybrid")}
    output = root / "artifacts" / f"retrieval-{time.time_ns()}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, {"dataset_sha256": dataset_sha256(cases), "fixture": fixture,
                         "granularity": "content_id + page", "summary": summary, "cases": results})
    print(json.dumps({"path": str(output), "summary": summary}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
