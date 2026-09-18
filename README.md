# CoursePilot AI Agent

Standalone AI backend for the team-built LearnHub LMS. The LMS owns users, courses,
uploads and the public PostgreSQL schema. This module owns Agent execution, retrieval,
conversation history, plans, observability and evaluation. See [UPSTREAM.md](UPSTREAM.md).

This repository is the canonical Agent implementation. The team LMS lives in
[personalized-learning-platform, branch ZHS](https://github.com/HousenZhu/personalized-learning-platform/tree/ZHS).
It is not copied into this personal repository.

## ReAct Upgrade

The current implementation uses model-selected tool calls, parallel learning snapshots,
native Ollama Thinking and evidence-validated answers. The model observes tool results
before choosing another tool or composing advice. See [execution, plan rules, configuration
and evaluation status](docs/react-upgrade.md). The 180 candidate cases have not been run
or frozen; the figures below belong to the previous dispatcher.

## Historical Measured Results

The completed Qwen3-8B Q4_K_M run contains **330 visible regression cases**, not an
independent held-out benchmark. On an RTX 3080 10GB with context 4096:

| Metric | Result |
| --- | --- |
| Task success | 278/330 (84.2%) |
| Source citation precision | 57/59 (96.6%) |
| Citation coverage | 55/66 (83.3%) |
| First validated response segment, median | 3.539s across 319 samples |

These are the [audited rescoring results](evals/artifacts/upgrade-regression-330-rescore-v2/report.md).
The [original run](evals/artifacts/upgrade-regression-330-v1/report.md) is retained:
the scorer initially matched short numbers inside random trace IDs. Rescoring changed
only that matching rule, not responses, labels or timings. One authorization-policy
assertion still fails; zero matched canary disclosures is not proof of universal safety.
Source correctness is not semantic entailment. See [verification and limits](docs/verification.md).

## Standalone Demo

Clone this repository directly; the isolated demo does not need real LMS data.
Install Docker Desktop (Linux containers) and Ollama, then run:

```bash
git clone https://github.com/HousenZhu/coursepilot-ai-agent.git
cd coursepilot-ai-agent
ollama pull qwen3:8b
docker compose -f docker-compose.eval.yml up --build -d
```

The API is available at `http://localhost:8001/docs`. The isolated fixture has two
students, separate learning records and original-text PDFs. Demo credentials are not
production secrets. See [evaluation protocol](docs/evaluation.md) before running any
candidate suite. Do not reseed an active request or evaluation.

## Runtime

```text
Next.js BFF / Better Auth -> short-lived JWT -> FastAPI
    -> reserve request + conversation lock -> request policy and course scope
    -> model chooses tools -> scoped tool results -> model decides again or answers
    -> deterministic records / validated source paragraphs
    -> atomic final response + messages + staged plan
PostgreSQL public: SELECT only; agent schema: application-owned writes
```

The four model-visible tools read learning snapshots, search course materials, read the
active plan, and stage a new plan. Independent reads execute concurrently, each with its
own database session. The model can inspect results and request another tool, within
four rounds and eight calls. Plan writes require trusted request intent and complete
learning evidence. There is no arbitrary SQL or multi-agent orchestration.

## Run With The LMS

From the parent LMS root, set `AGENT_INTERNAL_SECRET`, `BETTER_AUTH_SECRET` and a URL-safe
`AGENT_DB_PASSWORD` in the existing untracked `.env`. Keep secrets out of images.
Docker Desktop must use Linux containers on Windows; the same Compose files work on macOS.

```bash
docker compose up --build -d
docker compose logs --tail 60 agent-migrate agent web
```

Open http://localhost:3000. A one-shot migrator creates the Agent tables/checkpoints and
grants access to `coursepilot_agent`. The running Agent never receives migration credentials.
Existing PDFs must be reindexed to populate version metadata before upgraded retrieval
will return them. Use the existing teacher-owned indexing action.
Do not use `down -v` on the LMS stack to troubleshoot upgrades.

Native development uses Python 3.12 and `uv sync --frozen --extra dev`.
Set a migration-role `MIGRATION_DATABASE_URL` for `uv run alembic upgrade head`;
bootstrap once with `uv run python -m app.bootstrap`. Set runtime database URLs to the
restricted role and `CHECKPOINT_SETUP=false` before `uv run uvicorn app.main:app --reload`.

## API

- `POST /v1/agent/runs/stream`: existing message/course/conversation body; optional
  `Idempotency-Key` header. Same user/key/body returns the persisted final result;
  changed body or unfinished request returns 409. No automatic replay of failed writes.
- `GET /v1/agent/runs/{id}`: owner-only status and result.
- `GET /v1/conversations/{id}`: committed messages, citations and plans.
- `GET /v1/sources/{id}`: enrollment-checked PDF; changed/retired sources fail closed.
- `POST /internal/index/courses/{id}`: teacher-owned indexing only.
- `/health/live`, `/health/ready`, `/metrics`.

SSE retains `token/tool_status/final/error`. Tokens now contain checked text segments, not
raw model drafts. `final` adds run ID, explicit outcome and structured LMS fact tuples.
Source cards carry immutable source IDs and document versions. Source matching is not
proof that a natural-language claim is entailed by its evidence.

## Verification

Run from this directory. The test stack has its own database and network.

```bash
docker compose -f docker-compose.test.yml build tests
docker compose -f docker-compose.test.yml run --rm tests
docker compose -f docker-compose.test.yml run --rm -e RUN_INTEGRATION_TESTS=1 --entrypoint python tests -m pytest -q tests/test_tenant_isolation.py
docker compose -f docker-compose.test.yml down -v
```

The first suite covers migration, actual PDF ingestion, negative/positive ownership,
idempotency, cancellation, timeouts, rollback and controlled-model ReAct execution.
The legacy repository isolation suite runs separately because it replaces its fixture.
CI uses the locked container dependencies, Ruff and mypy.
See [previously executed checks and evidence limits](docs/verification.md) and the optional
[local metrics dashboard](docs/observability.md).

## Evaluation Status

The candidate ReAct suite contains 150 deterministic selections from the visible legacy
regression set and 30 new, unreviewed scenarios. It has not been executed or frozen.
The default ReAct runner uses `--split development` for the original 30 development
prompts and its legacy `--split heldout` spelling for the 180 visible candidates;
that spelling does not make them independent unseen cases. The earlier 330-case
reports measure the old dispatcher only. See [evaluation protocol](docs/evaluation.md)
and [the verification record](docs/verification.md).

## Limits

Single-process model concurrency is bounded; multiple replicas need a shared admission
controller. Conversation locks are database-backed across replicas.
Checkpoint threads are per run; committed product history reconstructs subsequent turns.
A process crash can leave a running row until a status read or later request detects its
released conversation lock and marks the abandoned run failed. Writes are never auto-replayed.
Disconnect cancels the application request, but provider-side GPU work may finish separately.
PDF layout/OCR, broad multilingual retrieval, semantic entailment guarantees and remote
load testing are not claimed. Histories retain a bounded recent context.
