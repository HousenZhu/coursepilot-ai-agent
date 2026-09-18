from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import hashlib
import re
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
from evals.audit import atomic_json, journal, snapshot_source, dependency_versions, fixture_snapshot, reset_case_plans


TARGETS = {
    "task_success": 0.85,
    "citation_precision": 0.90,
    "citation_coverage": 0.80,
}
ROOT = Path(__file__).resolve().parent.parent


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
    fault: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token_at: float | None = None
    final_at: float | None = None
    final: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    observed_tools: set[str] = set()
    streamed_text: list[str] = []
    visible_events: list[dict[str, Any]] = []
    http_error: Any = None
    status_code: int | None = None
    payload: dict[str, Any] = {"message": message, "course_id": course_id}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        async with client.stream(
            "POST",
            "/v1/agent/runs/stream",
            headers={"Authorization": f"Bearer {create_token(user_id)}", **({"X-Eval-Fault": fault} if fault else {})},
            json=payload,
        ) as response:
            status_code = response.status_code
            if status_code != 200:
                await response.aread()
                try:
                    http_error = response.json()
                except ValueError:
                    http_error = response.text
            response.raise_for_status()
            event_name = "message"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = json.loads(line.removeprefix("data:").strip())
                visible_events.append({"event": event_name, "data": data})
                if event_name == "token" and data.get("delta"):
                    streamed_text.append(str(data["delta"]))
                    if first_token_at is None:
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
        "http_error": http_error,
        "visible_events": visible_events,
        "observed_tools": sorted(observed_tools),
        "streamed_text": "".join(streamed_text),
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
    all_streamed: list[str] = []
    all_visible: list[dict[str, Any]] = []
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
            fault=case.get("fault"),
        )
        final = observation.get("final") or {}
        all_streamed.append(observation.get("streamed_text", ""))
        all_visible.extend(observation.get("visible_events", []))
        conversation_id = final.get("conversation_id", conversation_id)
        if observation.get("error"):
            break
    observation["streamed_text"] = "\n".join(all_streamed)
    observation["visible_events"] = all_visible
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


def _format_seconds(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}s"


def build_report(run_id: str, split: str, metrics: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        f"# CoursePilot Evaluation: {run_id}",
        "",
        "## Summary",
        "",
        f"- Split: `{split}`",
        f"- Cases: {metrics['total_cases']}",
        f"- Completion: {manifest.get('completed_cases', 0)}/{manifest.get('expected_cases', metrics['total_cases'])}",
        f"- Valid comparison: {manifest.get('valid_comparison', False)}",
        f"- Task success: {_format_rate(metrics['task_success'])}",
        f"- Tool routing accuracy: {_format_rate(metrics['tool_routing_accuracy'])}",
        f"- Grounding correctness: {_format_rate(metrics['grounding_correctness'])}",
        f"- Citation precision: {_format_rate(metrics['citation_precision'])}",
        f"- Citation coverage: {_format_rate(metrics['citation_coverage'])}",
        f"- Citation counts: {metrics['citation_correct_count']}/{metrics['citation_returned_count']} correct; "
        f"{metrics['citation_covered_case_count']}/{metrics['citation_required_case_count']} required cases covered",
        f"- Authorization pass rate: {_format_rate(metrics['authorization_pass_rate'])}",
        f"- Authorization leaks: {metrics['authorization_leaks']}",
        f"- Error rate: {_format_rate(metrics['error_rate'])}",
        f"- Median first verified text: {_format_seconds(metrics['ttft_p50_seconds'])}",
        f"- First-verified-text samples: {metrics['ttft_sample_count']}",
        f"- p95 first verified text: {_format_seconds(metrics['ttft_p95_seconds'])}",
        f"- Median end-to-end latency: {_format_seconds(metrics['latency_p50_seconds'])}",
        f"- p95 end-to-end latency: {_format_seconds(metrics['latency_p95_seconds'])}",
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
    cases = (json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else
             expand_templates(load_templates(args.templates), args.split))
    if not args.cases and args.suite == "react":
        from evals.react_suite import adapt_case, new_cases, select_regression
        development = [adapt_case(c) for c in expand_templates(load_templates(args.templates), "development")]
        regression, _ = select_regression(args.templates)
        cases = development if args.split == "development" else regression + new_cases()
        if args.split == "all":
            cases = development + cases
    if args.template_id:
        if args.split != "development" or args.cases:
            raise ValueError("Template filtering is development-only")
        cases = [case for case in cases if case["template_id"] == args.template_id]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Cases must have unique IDs and must not be empty")
    dataset_hash = dataset_sha256(cases)
    role = "development" if args.split == "development" and not args.cases else "visible_regression"
    if args.review:
        review = json.loads(args.review.read_text(encoding="utf-8"))
        if not args.cases or review.get("dataset_sha256") != dataset_hash or not review.get("reviewer") or not review.get("approved"):
            raise ValueError("A reviewed final set needs external cases and matching human approval")
        role = "reviewed_final"
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Unsafe run ID")
    artifact_dir = args.output_dir / run_id
    artifact_dir.mkdir(parents=True, exist_ok=args.resume)
    root = ROOT
    software_hash = snapshot_source(root, artifact_dir / ("source-resume.zip" if args.resume else "source.zip"))
    fixture = await fixture_snapshot()
    fixture_hash = hashlib.sha256(json.dumps(fixture, sort_keys=True, default=str).encode()).hexdigest()
    model = await ollama_metadata()
    fingerprint = {
        "dataset": dataset_hash, "software": software_hash, "fixture": fixture_hash,
        "model_digest": (model.get("installed_model") or {}).get("digest"),
        "dependencies": dependency_versions(),
        "settings": {name: os.getenv(name) for name in (
            "LLM_MODEL", "LLM_PROVIDER", "LLM_CONTEXT_SIZE", "LLM_TEMPERATURE", "LLM_MAX_TOKENS", "LLM_DISABLE_THINKING",
            "LLM_TIMEOUT_SECONDS", "RUN_TIMEOUT_SECONDS", "RETRIEVAL_MODE", "EMBEDDING_MODEL")},
    }
    manifest_path = artifact_dir / "manifest.json"
    results = []
    if args.resume:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["fingerprint"] != fingerprint:
            raise ValueError("Resume rejected: source/model/settings/dependencies/fixture changed")
        progress = artifact_dir / "results.json"
        if progress.exists():
            results = json.loads(progress.read_text(encoding="utf-8"))["cases"]
        events = [json.loads(line) for line in (artifact_dir / "attempts.jsonl").read_text().splitlines()]
        finished = {result["id"] for result in results}
        interrupted = {event["case_id"] for event in events if event["event"] == "case_started"} - finished
        for case in cases:
            if case["id"] in interrupted:
                results.append(score_case(case, {"error": {"type": "InterruptedAttempt"}}))
    else:
        manifest = {
            "run_id": run_id, "started_at_utc": datetime.now(UTC).isoformat(), "split": args.split,
            "dataset_role": role, "dataset_sha256": dataset_hash, "fingerprint": fingerprint, "suite": args.suite,
            "expected_cases": len(cases),
            "git_commit": os.getenv("EVAL_GIT_COMMIT", "working-tree"),
            "model": {"name": os.getenv("LLM_MODEL", "qwen3:8b"), "ollama": model},
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0")), "concurrency": 1,
            "warmups": 0, "ttft_definition": "first validated nonempty answer segment; includes no-record/refusal text",
            "hardware": {key: os.getenv(value, "unknown") for key, value in (
                ("gpu", "EVAL_GPU_NAME"), ("driver", "EVAL_GPU_DRIVER"), ("memory_mb", "EVAL_GPU_MEMORY_MB"))},
            "targets_not_results": TARGETS,
        }
        atomic_json(artifact_dir / "dataset.json", cases)
        atomic_json(artifact_dir / "fixture.json", fixture)
        atomic_json(manifest_path, manifest)
    journal_path = artifact_dir / "attempts.jsonl"
    journal(journal_path, "session_started", resume=args.resume, platform=platform.platform())
    user_id = os.environ["EVAL_USER_ID"]
    try:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout, connect=10)) as client:
            await wait_until_ready(client)
            for index in range(args.warmups):
                journal(journal_path, "warmup_started", index=index)
                warmup = await _read_sse(client, user_id=user_id, message="Reply with only: ready",
                                        course_id=None, conversation_id=None)
                manifest["warmups"] += 1
                journal(journal_path, "warmup_finished", observation=warmup)
                atomic_json(manifest_path, manifest)
                if warmup.get("error") or not warmup.get("final"):
                    raise RuntimeError("Warm-up failed; measurement not started")
            warmed_model = await ollama_metadata()
            running_model = next((item for item in warmed_model.get("running_models", [])
                                  if item.get("name") == manifest["model"]["name"]), {})
            runtime_context = {key: running_model.get(key) for key in ("digest", "context_length")}
            if args.resume and manifest.get("runtime_context") != runtime_context:
                raise ValueError("Resume rejected: actual loaded model/context changed")
            manifest["runtime_context"] = runtime_context
            manifest["model"]["after_warmup"] = warmed_model
            atomic_json(manifest_path, manifest)
            finished = {result["id"] for result in results}
            for case in cases:
                if case["id"] in finished:
                    continue
                await reset_case_plans()
                journal(journal_path, "case_started", case_id=case["id"])
                try:
                    async with asyncio.timeout(args.timeout):
                        result = await run_case(client, case, user_id)
                except Exception as exc:
                    result = score_case(case, {"error": {"type": type(exc).__name__}})
                results.append(result)
                journal(journal_path, "case_finished", case_id=case["id"], result=result)
                atomic_json(artifact_dir / "results.json", {"metrics": aggregate_results(results), "cases": results})
                print(f"[{len(results):03d}/{len(cases):03d}] {case['id']} {'PASS' if result['task_success'] else 'FAIL'}", flush=True)
    finally:
        metrics = aggregate_results(results)
        manifest["completed_cases"] = len(results)
        manifest["complete"] = len(results) == len(cases)
        manifest["source_unchanged"] = snapshot_source(root, artifact_dir / "source-end.zip") == software_hash
        manifest["valid_comparison"] = manifest["complete"] and manifest["source_unchanged"]
        manifest["ended_at_utc"] = datetime.now(UTC).isoformat()
        atomic_json(artifact_dir / "results.json", {"metrics": metrics, "cases": results})
        atomic_json(manifest_path, manifest)
        (artifact_dir / "report.md").write_text(build_report(run_id, role, metrics, manifest), encoding="utf-8")
    return artifact_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the auditable CoursePilot evaluation")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--cases", type=Path, help="External versioned case list")
    parser.add_argument("--review", type=Path, help="Human review approval for an external frozen set")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--templates", type=Path, default=Path(__file__).with_name("templates.jsonl")
    )
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="heldout")
    parser.add_argument("--suite", choices=["react", "legacy"], default="react")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("artifacts"))
    parser.add_argument("--run-id")
    parser.add_argument("--template-id", help="Run one template for development diagnostics")
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=390)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(async_main(parse_args()))
