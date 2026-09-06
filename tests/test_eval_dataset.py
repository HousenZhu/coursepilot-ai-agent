from pathlib import Path

from evals.dataset import dataset_sha256, expand_templates, load_templates


TEMPLATES = Path(__file__).parents[1] / "evals" / "templates.jsonl"


def test_dataset_has_30_templates_and_330_heldout_cases() -> None:
    templates = load_templates(TEMPLATES)
    development = expand_templates(templates, "development")
    heldout = expand_templates(templates, "heldout")

    assert len(templates) == 30
    assert all(len(template["variants"]) == 12 for template in templates)
    assert len(development) == 30
    assert len(heldout) == 330
    assert {case["prompt"] for case in development}.isdisjoint(
        {case["prompt"] for case in heldout}
    )


def test_dataset_hash_is_stable_and_order_sensitive() -> None:
    cases = expand_templates(load_templates(TEMPLATES), "heldout")
    digest = dataset_sha256(cases)

    assert len(digest) == 64
    assert digest == dataset_sha256(cases)
    assert digest != dataset_sha256(list(reversed(cases)))
