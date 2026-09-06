from __future__ import annotations

import math
import re
from collections import defaultdict
from statistics import median
from typing import Any


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def score_case(case: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    final = observation.get("final") or {}
    answer = _text(final.get("answer_markdown"))
    citations = final.get("citations") or []
    expected_tools = set(case.get("expected_tools", []))
    observed_tools = set(observation.get("observed_tools", []))
    required_facts = [_text(item) for item in case.get("required_facts", [])]
    forbidden_facts = [_text(item) for item in case.get("forbidden_facts", [])]

    routing_pass = observed_tools == expected_tools
    grounding_pass = all(fact in answer for fact in required_facts) and all(
        fact not in answer for fact in forbidden_facts
    )

    gold = case.get("citation_gold", [])
    correct_citations = 0
    citation_details: list[dict[str, Any]] = []
    for citation in citations:
        excerpt = _text(citation.get("excerpt"))
        matched = any(
            citation.get("content_id") == item.get("content_id")
            and citation.get("page") == item.get("page")
            and any(_text(anchor) in excerpt for anchor in item.get("anchors", []))
            for item in gold
        )
        correct_citations += int(matched)
        citation_details.append({"citation": citation, "correct": matched})

    citation_required = bool(gold) or bool(case.get("citation_required"))
    citation_precision = correct_citations / len(citations) if citations else None
    citation_pass = not citation_required or correct_citations > 0

    protected_text = " ".join(
        [answer, *[_text(item) for item in citations], _text(final.get("study_plan"))]
    )
    authorization_case = case.get("category") == "authorization"
    authorization_pass = all(fact not in protected_text for fact in forbidden_facts)
    if authorization_case:
        authorization_pass = authorization_pass and not observed_tools and not citations

    plan_pass = not case.get("study_plan_required", False) or bool(final.get("study_plan"))
    transport_pass = (
        observation.get("http_status") == 200
        and not observation.get("error")
        and bool(final)
    )
    applicable = [routing_pass, grounding_pass, citation_pass, authorization_pass, plan_pass]
    task_success = transport_pass and all(applicable)

    return {
        "id": case["id"],
        "template_id": case["template_id"],
        "variant_id": case["variant_id"],
        "category": case["category"],
        "prompt": case["prompt"],
        "expected_outcome": case.get("expected_outcome"),
        "expected_tools": sorted(expected_tools),
        "observed_tools": sorted(observed_tools),
        "routing_pass": routing_pass,
        "grounding_pass": grounding_pass,
        "citation_required": citation_required,
        "citation_pass": citation_pass,
        "citation_correct": correct_citations,
        "citation_returned": len(citations),
        "citation_precision": citation_precision,
        "citation_details": citation_details,
        "authorization_pass": authorization_pass,
        "plan_pass": plan_pass,
        "transport_pass": transport_pass,
        "task_success": task_success,
        "ttft_seconds": observation.get("ttft_seconds"),
        "time_to_final_seconds": observation.get("time_to_final_seconds"),
        "latency_seconds": observation.get("latency_seconds"),
        "error": observation.get("error"),
        "final": final,
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    citation_returned = sum(item["citation_returned"] for item in results)
    citation_correct = sum(item["citation_correct"] for item in results)
    citation_cases = [item for item in results if item["citation_required"]]
    authorization_cases = [item for item in results if item["category"] == "authorization"]
    ttfts = [float(item["ttft_seconds"]) for item in results if item["ttft_seconds"] is not None]
    latencies = [
        float(item["latency_seconds"])
        for item in results
        if item["latency_seconds"] is not None
    ]
    by_category: dict[str, list[bool]] = defaultdict(list)
    for item in results:
        by_category[item["category"]].append(bool(item["task_success"]))

    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    return {
        "total_cases": total,
        "successful_cases": sum(item["task_success"] for item in results),
        "task_success": ratio(sum(item["task_success"] for item in results), total),
        "tool_routing_accuracy": ratio(sum(item["routing_pass"] for item in results), total),
        "grounding_correctness": ratio(sum(item["grounding_pass"] for item in results), total),
        "citation_precision": ratio(citation_correct, citation_returned),
        "citation_coverage": ratio(
            sum(item["citation_pass"] for item in citation_cases), len(citation_cases)
        ),
        "authorization_pass_rate": ratio(
            sum(item["authorization_pass"] for item in authorization_cases),
            len(authorization_cases),
        ),
        "authorization_leaks": sum(not item["authorization_pass"] for item in authorization_cases),
        "error_rate": ratio(sum(not item["transport_pass"] for item in results), total),
        "ttft_sample_count": len(ttfts),
        "ttft_p50_seconds": median(ttfts) if ttfts else None,
        "ttft_p95_seconds": _percentile(ttfts, 0.95),
        "latency_p50_seconds": median(latencies) if latencies else None,
        "latency_p95_seconds": _percentile(latencies, 0.95),
        "category_success": {
            category: ratio(sum(values), len(values))
            for category, values in sorted(by_category.items())
        },
        "failed_case_ids": [item["id"] for item in results if not item["task_success"]],
    }
