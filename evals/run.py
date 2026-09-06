from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import jwt

from evals.dataset import dataset_sha256, expand_templates, load_templates
from evals.scoring import aggregate_results, score_case


TARGETS = {
    "task_success": 0.88,
    "citation_precision": 0.95,
    "ttft_p50_seconds": 1.4,
}


def create_token(user_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "role": "STUDENT",
            "iss": os.getenv("AGENT_JWT_ISSUER", "learnhub-web"),
            "aud": os.getenv("AGENT_JWT_AUDIENCE", "coursepilot-agent"),
            "iat": now,
            "exp": now + timedelta(seconds=60),
            "jti": uuid4().hex,
        },
        os.environ["AGENT_INTERNAL_SECRET"],
        algorithm="HS256",
    )


async def _read_sse(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    message: str,
    course_id: str | None,
    conversation_id: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token_at: float | None = None
    final_at: float | None = None
    final: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    observed_tools: set[str] = set()
    status_code: int | None = None
    payload: dict[str, Any] = {"message": message, "course_id": course_id}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        async with client.stream(
            "POST",
            "/v1/agent/runs/stream",
            headers={"Authorization": f"Bearer {create_token(user_id)}"},
            json=payload,
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            event_name = "message"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = json.loads(line.removeprefix("data:").strip())
                if event_name == "token" and first_token_at is None:
                    first_token_at = time.perf_counter()
                elif event_name == "tool_status" and data.get("status") == "started":
                    observed_tools.add(str(data.get("name")))
                elif event_name == "final":
                    final = data
                    final_at = time.perf_counter()
                elif event_name == "error":
                    error = data
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}

    ended = time.perf_counter()
    return {
        "http_status": status_code,
        "observed_tools": sorted(observed_tools),
        "final": final,
        "error": error,
        "ttft_seconds": first_token_at - started if first_token_at is not None else None,
        "time_to_final_seconds": final_at - started if final_at is not None else None,
        "latency_seconds": ended - started,
    }


async def run_case(
    client: httpx.AsyncClient, case: dict[str, Any], user_id: str
) -> dict[str, Any]:
    conversation_id: str | None = None
    observation: dict[str, Any] = {}
    course_id = case.get("course_id")
    if course_id == "${COURSE_ID}":
        course_id = os.getenv("EVAL_COURSE_ID")

    for turn in case["turns"]:
        observation = await _read_sse(
            client,
            user_id=user_id,
            message=turn,
            course_id=course_id,
            conversation_id=conversation_id,
        )
        final = observation.get("final") or {}
        conversation_id = final.get("conversation_id", conversation_id)
        if observation.get("error"):
            break
    return score_case(case, observation)


async def warm_up(client: httpx.AsyncClient, user_id: str, count: int) -> None:
    for _ in range(count):
        await _read_sse(
            client,
            user_id=user_id,
            message="Reply with only: ready",
            course_id=None,
            conversation_id=None,
        )


async def wait_until_ready(client: httpx.AsyncClient, timeout_seconds: float = 90) -> None:
    deadline = time.perf_counter() + timeout_seconds
    last_error: Exception | None = None
    while time.perf_counter() < deadline:
        try:
            response = await client.get("/health/ready")
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(1)
    raise TimeoutError(f"Agent did not become ready within {timeout_seconds}s: {last_error}")


async def ollama_metadata() -> dict[str, Any]:
    configured = os.getenv("LLM_BASE_URL", "http://host.docker.internal:11434/v1")
    parts = urlsplit(configured)
    root = f"{parts.scheme}://{parts.netloc}"
    metadata: dict[str, Any] = {"base_url": root}
    try:
        async with httpx.AsyncClient(base_url=root, timeout=10) as client:
            tags = (await client.get("/api/tags")).json()
            running = (await client.get("/api/ps")).json()
        model_name = os.getenv("LLM_MODEL", "qwen3:8b")
        model = next(
            (item for item in tags.get("models", []) if item.get("name") == model_name),
            None,
        )
        metadata.update({"installed_model": model, "running_models": running.get("models", [])})
    except Exception as exc:
        metadata["metadata_error"] = f"{type(exc).__name__}: {exc}"
    return metadata


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def build_report(run_id: str, split: str, metrics: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        f"# CoursePilot Evaluation: {run_id}",
        "",
        "## Summary",
        "",
        f"- Split: `{split}`",
        f"- Cases: {metrics['total_cases']}",
        f"- Task success: {_format_rate(metrics['task_success'])}",
        f"- Tool routing accuracy: {_format_rate(metrics['tool_routing_accuracy'])}",
        f"- Grounding correctness: {_format_rate(metrics['grounding_correctness'])}",
        f"- Citation precision: {_format_rate(metrics['citation_precision'])}",
        f"- Citation coverage: {_format_rate(metrics['citation_coverage'])}",
        f"- Authorization pass rate: {_format_rate(metrics['authorization_pass_rate'])}",
        f"- Authorization leaks: {metrics['authorization_leaks']}",
        f"- Error rate: {_format_rate(metrics['error_rate'])}",
        f"- Median TTFT: {metrics['ttft_p50_seconds'] or 0:.3f}s",
        f"- p95 TTFT: {metrics['ttft_p95_seconds'] or 0:.3f}s",
        f"- Median end-to-end latency: {metrics['latency_p50_seconds'] or 0:.3f}s",
        f"- p95 end-to-end latency: {metrics['latency_p95_seconds'] or 0:.3f}s",
        "",
        "## Category Success",
        "",
    ]
    lines.extend(
        f"- {category}: {_format_rate(rate)}"
        for category, rate in metrics["category_success"].items()
    )
    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            f"- Dataset SHA-256: `{manifest['dataset_sha256']}`",
            f"- Git commit: `{manifest['git_commit']}`",
            f"- Model: `{manifest['model']['name']}`",
            f"- Temperature: `{manifest['temperature']}`",
            f"- Concurrency: `{manifest['concurrency']}`",
            f"- Warm-up requests: `{manifest['warmups']}`",
            "",
            "## Failed Cases",
            "",
        ]
    )
    lines.extend(f"- `{case_id}`" for case_id in metrics["failed_case_ids"])
    if not metrics["failed_case_ids"]:
        lines.append("None.")
    return "\n".join(lines) + "\n"


async def async_main(args: argparse.Namespace) -> Path:
    templates = load_templates(args.templates)
    cases = expand_templates(templates, args.split)
    if args.template_id:
        cases = [case for case in cases if case["template_id"] == args.template_id]
        if not cases:
            raise ValueError(f"Unknown template_id: {args.template_id}")
    dataset_hash = dataset_sha256(cases)
    user_id = os.environ["EVAL_USER_ID"]
    timeout = httpx.Timeout(args.timeout, connect=10)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
        await wait_until_ready(client)
        await warm_up(client, user_id, args.warmups)
        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            result = await run_case(client, case, user_id)
            results.append(result)
            print(
                f"[{index:03d}/{len(cases):03d}] {case['id']} "
                f"{'PASS' if result['task_success'] else 'FAIL'}",
                flush=True,
            )

    metrics = aggregate_results(results)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = args.output_dir / run_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": run_id,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "split": args.split,
        "dataset_sha256": dataset_hash,
        "dataset_source": str(args.templates),
        "git_commit": os.getenv("EVAL_GIT_COMMIT", "working-tree"),
        "model": {
            "name": os.getenv("LLM_MODEL", "qwen3:8b"),
            "requested_quantization": "Q4_K_M",
            "ollama": await ollama_metadata(),
        },
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0")),
        "thinking_disabled": os.getenv("LLM_DISABLE_THINKING", "false").lower() == "true",
        "concurrency": 1,
        "warmups": args.warmups,
        "hardware": {
            "gpu": os.getenv("EVAL_GPU_NAME", "unknown"),
            "gpu_memory_mb": os.getenv("EVAL_GPU_MEMORY_MB", "unknown"),
            "driver": os.getenv("EVAL_GPU_DRIVER", "unknown"),
            "runner_platform": platform.platform(),
        },
        "targets_not_results": TARGETS,
    }
    result_payload = {"metrics": metrics, "cases": results}
    (artifact_dir / "results.json").write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifact_dir / "report.md").write_text(
        build_report(run_id, args.split, metrics, manifest), encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2), flush=True)
    return artifact_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the auditable CoursePilot evaluation")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--templates", type=Path, default=Path(__file__).with_name("templates.jsonl")
    )
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="heldout")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("artifacts"))
    parser.add_argument("--run-id")
    parser.add_argument("--template-id", help="Run one template for development diagnostics")
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(async_main(parse_args()))
