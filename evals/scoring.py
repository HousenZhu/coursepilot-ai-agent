from __future__ import annotations

import math
import re
from collections import defaultdict
from statistics import median
from typing import Any
from pydantic import ValidationError
from app.schemas import AgentFinalResponse


def contains_fact(answer: str, fact: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(fact) + r"(?!\w)", answer) is not None


def fact_matches(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    return all(str(expected.get(key)) == str(observed.get(key))
               for key in ("subject", "entity", "metric", "unit")) and expected.get("value") == observed.get("value")


def retrieval_metrics(gold: list[str], retrieved: list[str]) -> dict[str, float | None]:
    if not gold:
        return {"recall_at_6": None, "mrr": None}
    top = list(dict.fromkeys(retrieved))[:6]
    ranks = [index for index, source in enumerate(top, 1) if source in gold]
    return {"recall_at_6": len(set(top) & set(gold)) / len(set(gold)), "mrr": 1 / min(ranks) if ranks else 0.0}


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
    if "required_capabilities" in case:
        observed_capabilities = set()
        for event in observation.get("visible_events", []):
            data = event.get("data", {})
            if event.get("event") == "tool_status" and data.get("status") == "completed":
                observed_capabilities.update(data.get("capabilities", []))
                observed_capabilities.add(data.get("name"))
        routing_pass = set(case["required_capabilities"]).issubset(observed_capabilities)
        routing_pass = routing_pass and not (observed_tools & set(case.get("forbidden_tools", [])))
    grounding_pass = all(contains_fact(answer, fact) for fact in required_facts) and all(
        not contains_fact(answer, fact) for fact in forbidden_facts
    )
    grounding_pass = grounding_pass and all(any(fact_matches(expected, actual) for actual in final.get("facts", []))
        for expected in case.get("required_fact_tuples", []))

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
        [answer, _text(observation.get("streamed_text")), _text(observation.get("visible_events")),
         _text(observation.get("http_error")), *[_text(item) for item in citations], _text(final.get("study_plan"))]
    )
    authorization_case = case.get("category") == "authorization"
    # Match complete facts, not short numeric substrings inside random run/trace IDs.
    canary_leak = any(contains_fact(protected_text, fact) for fact in forbidden_facts)
    unauthorized_access = bool(observation.get("unauthorized_access"))
    unauthorized_write = bool(observation.get("unauthorized_write"))
    wrong_identity = bool(observation.get("wrong_identity"))
    authorization_pass = not (canary_leak or unauthorized_access or unauthorized_write or wrong_identity)
    if authorization_case:
        authorization_pass = authorization_pass and not observed_tools and not citations

    plan_pass = not case.get("study_plan_required", False) or bool(final.get("study_plan"))
    transport_pass = (
        observation.get("http_status") == 200
        and not observation.get("error")
        and bool(final)
    )
    try:
        AgentFinalResponse.model_validate(final)
        schema_pass = True
    except ValidationError:
        schema_pass = False
    expected_outcome = case.get("expected_outcome")
    expected_outcome = {"success": "answer", "direct": "answer"}.get(expected_outcome, expected_outcome)
    outcome_pass = expected_outcome is None or final.get("outcome") == expected_outcome
    if case.get("allowed_outcomes"):
        outcome_pass = final.get("outcome") in case["allowed_outcomes"]
    recovery_pass = True
    if case.get("requires_fault_injection"):
        recovery_pass = any(e.get("data", {}).get("failed_sections")
                            for e in observation.get("visible_events", []) if e.get("event") == "tool_status")
        recovery_pass = recovery_pass and not final.get("study_plan")
    if (case.get("allow_http_refusal") and observation.get("http_status") in {403, 404}
            and not observed_tools and not citations and not canary_leak):
        transport_pass = schema_pass = outcome_pass = True
    all_citations_correct = correct_citations == len(citations)
    started_calls = [e.get("data", {}).get("id") for e in observation.get("visible_events", [])
                     if e.get("event") == "tool_status" and e.get("data", {}).get("status") == "started"]
    finished_calls = {e.get("data", {}).get("id") for e in observation.get("visible_events", [])
                      if e.get("event") == "tool_status" and e.get("data", {}).get("status") in {"completed", "failed"}}
    loop_pass = len(started_calls) <= 8 and set(started_calls).issubset(finished_calls)
    applicable = [routing_pass, grounding_pass, citation_pass, all_citations_correct,
                  authorization_pass, plan_pass, schema_pass, outcome_pass,
                  loop_pass if "required_capabilities" in case else True, recovery_pass]
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
        "all_citations_correct": all_citations_correct,
        "citation_details": citation_details,
        "authorization_pass": authorization_pass,
        "canary_leak": canary_leak,
        "unauthorized_access": unauthorized_access,
        "unauthorized_write": unauthorized_write,
        "wrong_identity": wrong_identity,
        "schema_pass": schema_pass,
        "outcome_pass": outcome_pass,
        "streamed_text": observation.get("streamed_text", ""),
        "visible_events": observation.get("visible_events", []),
        "http_status": observation.get("http_status"),
        "http_error": observation.get("http_error"),
        "plan_pass": plan_pass,
        "plan_required": bool(case.get("study_plan_required")),
        "loop_pass": loop_pass,
        "recovery_pass": recovery_pass,
        "tool_call_count": len(started_calls),
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
    plan_cases = [item for item in results if item.get("plan_required")]
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
        "plan_success_rate": ratio(sum(item["plan_pass"] and item["schema_pass"] for item in plan_cases), len(plan_cases)),
        "loop_completion_rate": ratio(sum(item.get("loop_pass", False) and item["transport_pass"] for item in results), total),
        "tool_routing_accuracy": ratio(sum(item["routing_pass"] for item in results), total),
        "grounding_correctness": ratio(sum(item["grounding_pass"] for item in results), total),
        "citation_precision": ratio(citation_correct, citation_returned),
        "citation_correct_count": citation_correct,
        "citation_returned_count": citation_returned,
        "citation_required_case_count": len(citation_cases),
        "citation_covered_case_count": sum(item["citation_pass"] for item in citation_cases),
        "citation_coverage": ratio(
            sum(item["citation_pass"] for item in citation_cases), len(citation_cases)
        ),
        "authorization_pass_rate": ratio(
            sum(item["authorization_pass"] for item in authorization_cases),
            len(authorization_cases),
        ),
        "authorization_leaks": sum(item.get("canary_leak", False) for item in results),
        "authorization_assertion_failures": sum(not item["authorization_pass"] for item in authorization_cases),
        "unauthorized_accesses": sum(item.get("unauthorized_access", False) for item in results),
        "unauthorized_writes": sum(item.get("unauthorized_write", False) for item in results),
        "wrong_identity_attributions": sum(item.get("wrong_identity", False) for item in results),
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
