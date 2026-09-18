from pathlib import Path

from evals.dataset import dataset_sha256
from evals.react_suite import new_cases, select_regression
from evals.scoring import score_case


def test_selection_is_balanced_stable_and_not_score_based():
    path = Path(__file__).parents[1] / "evals" / "templates.jsonl"
    cases, changes = select_regression(path)
    assert len(cases) == len(changes) == 150
    assert len({c["id"] for c in cases}) == 150
    assert len({c["template_id"] for c in cases}) == 30
    assert all(sum(c["template_id"] == template for c in cases) == 5 for template in {c["template_id"] for c in cases})
    assert dataset_sha256(cases) == dataset_sha256(select_regression(path)[0])
    assert all(c["variant_id"] != 0 for c in cases)


def test_new_cases_are_separate_and_explicitly_unreviewed():
    cases = new_cases()
    assert len(cases) == len({c["prompt"] for c in cases}) == 30
    assert all(c["review_status"] == "pending" for c in cases)
    assert sum(c["requires_fault_injection"] for c in cases) == 5


def test_expected_http_refusal_does_not_require_fake_final():
    case = {"id": "refusal", "template_id": "refusal", "variant_id": 1,
            "category": "authorization", "prompt": "private course",
            "expected_outcome": "refuse", "allow_http_refusal": True,
            "required_capabilities": [], "forbidden_facts": ["SECRET"]}
    result = score_case(case, {"http_status": 403, "http_error": {"detail": "Forbidden"}})
    assert result["task_success"]
    assert not score_case(case, {"http_status": 403, "http_error": "SECRET"})["task_success"]


def test_snapshot_routing_requires_completed_sections():
    case = {"id": "records", "template_id": "records", "variant_id": 1,
            "category": "routing", "prompt": "grades", "required_capabilities": ["assessments"]}
    observation = {"observed_tools": ["get_learning_snapshot"], "visible_events": [
        {"event": "tool_status", "data": {"name": "get_learning_snapshot", "status": "completed", "capabilities": ["profile"]}}]}
    assert not score_case(case, observation)["routing_pass"]
    observation["visible_events"][0]["data"]["capabilities"].append("assessments")
    assert score_case(case, observation)["routing_pass"]
