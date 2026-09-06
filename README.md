# CoursePilot AI Agent

CoursePilot is a standalone Python AI-agent service for personalized learning support. It was developed as an extension to the team-built **LearnHub LMS**, where users, enrollments, courses, assessments, assignments, deadlines, and PDF materials already exist in PostgreSQL.

The original LMS project remains the system of record and UI host: [personalized-learning-platform](https://github.com/HousenZhu/personalized-learning-platform).

This repository intentionally contains only the CoursePilot Agent service, its migrations, evaluation harness, and integration documentation. It does not redistribute the original Next.js LMS application, its database, user data, or team-owned frontend code.

## What It Adds To The LMS

CoursePilot turns existing LMS data into permission-scoped, typed Agent tools rather than injecting one unstructured context string into a chatbot prompt. It supports learning diagnostics, saved study plans, course-material retrieval with citations, streaming answers, and persisted multi-turn conversations.

```text
LearnHub LMS BFF -> short-lived internal JWT -> CoursePilot FastAPI
                                             -> LangGraph routing and tool loop
                                             -> read-only LMS PostgreSQL + pgvector
                                             -> SSE answer, citations, and study plan
```

The Agent is intentionally a bounded single-agent workflow:

```text
validate -> route -> tools/retrieve -> answer -> verify -> persist
```

It uses an explicit tool allowlist, server-injected identity, fixed repository queries, and a four-round tool limit. The model never receives a user ID and cannot generate arbitrary SQL.

## Integration Contract

The LMS BFF authenticates its Better Auth session and issues a 60-second HS256 internal JWT. CoursePilot verifies `sub`, `role`, `iss`, `aud`, `iat`, `exp`, and `jti`; browser clients do not call the Python service directly.

`POST /v1/agent/runs/stream` accepts:

```json
{
  "conversation_id": null,
  "message": "Use my grades and deadlines to create a study plan",
  "course_id": null
}
```

It emits SSE `token`, `tool_status`, `final`, and `error` events. The final event contains Markdown, source citations, an optional persisted study plan, suggested actions, and a trace ID. See [UPSTREAM.md](UPSTREAM.md) for the LMS schema and deployment assumptions.

## Local Development

Prerequisites: Python 3.12, PostgreSQL with pgvector, and an OpenAI-compatible chat endpoint. For local use, Ollama is supported.

```bash
cp .env.example .env
python -m venv .venv
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

Set `DATABASE_URL` to a PostgreSQL database that exposes the LMS tables as read-only to the Agent database role. The Agent writes only to its `agent` schema.

## Isolated Evaluation

The included evaluation stack never uses the team LMS database. It starts a temporary pgvector database, seeds two isolated students and canary data, then runs a 30-template dataset with 12 variants per template. Variant 0 is development-only; variants 1-11 make up the 330-case held-out set.

```bash
docker compose -f docker-compose.eval.yml up --build -d
docker compose -f docker-compose.eval.yml exec -T eval-agent python -m evals.seed --reset
docker compose -f docker-compose.eval.yml exec -T eval-agent python -m pytest -q
docker compose -f docker-compose.eval.yml exec -T eval-agent python -m evals.run --split heldout
docker compose -f docker-compose.eval.yml down -v
```

Each run writes a case-level JSON result, Markdown report, and environment manifest under `evals/artifacts/`. Metrics are calculated deterministically: exact tool routing, required and forbidden facts, citation precision over every returned citation, authorization isolation, TTFT, and end-to-end latency.

## Repository Scope

- `app/`: FastAPI endpoints, LangGraph workflow, typed tools, repositories, RAG, and observability.
- `alembic/`: Agent-schema migrations.
- `evals/`: fixtures, 30 scenario templates, deterministic scorers, and SSE runner.
- `tests/`: unit, routing, authorization, and isolated database tests.
- `docs/`: system architecture and interview/demo material.

## Deliberate Limits

V1 does not use multi-agent orchestration, arbitrary SQL, web search, OCR, queues, or model fine-tuning. Exact pgvector cosine search is used for the small demonstration corpus; an HNSW index is a measured future scaling option, not a default complexity cost.
