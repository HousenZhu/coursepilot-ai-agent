from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal


Split = Literal["development", "heldout", "all"]


def _normalized_prompt(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def load_templates(path: Path) -> list[dict[str, Any]]:
    templates = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validate_templates(templates)
    return templates


def validate_templates(templates: list[dict[str, Any]]) -> None:
    if len(templates) != 30:
        raise ValueError(f"Expected 30 templates, found {len(templates)}")

    template_ids: set[str] = set()
    prompts: dict[str, str] = {}
    for template in templates:
        template_id = str(template.get("template_id", "")).strip()
        if not template_id or template_id in template_ids:
            raise ValueError(f"Missing or duplicate template_id: {template_id!r}")
        template_ids.add(template_id)

        variants = template.get("variants")
        if not isinstance(variants, list) or len(variants) != 12:
            raise ValueError(f"{template_id} must contain exactly 12 variants")
        local_prompts = {_normalized_prompt(str(prompt)) for prompt in variants}
        if len(local_prompts) != 12:
            raise ValueError(f"{template_id} contains duplicate variants")
        for prompt in variants:
            normalized = _normalized_prompt(str(prompt))
            if normalized in prompts:
                raise ValueError(
                    f"Duplicate prompt in {template_id} and {prompts[normalized]}: {prompt!r}"
                )
            prompts[normalized] = template_id


def expand_templates(
    templates: list[dict[str, Any]], split: Split = "heldout"
) -> list[dict[str, Any]]:
    indices = {
        "development": range(0, 1),
        "heldout": range(1, 12),
        "all": range(0, 12),
    }[split]
    cases: list[dict[str, Any]] = []
    for template in templates:
        shared = {key: value for key, value in template.items() if key != "variants"}
        for variant_id in indices:
            prompt = template["variants"][variant_id]
            cases.append(
                {
                    **shared,
                    "id": f"{template['template_id']}::v{variant_id:02d}",
                    "variant_id": variant_id,
                    "turns": [*template.get("context_turns", []), prompt],
                    "prompt": prompt,
                }
            )
    return cases


def dataset_sha256(cases: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        cases,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
