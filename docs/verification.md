# Verification Record

## Scope

These checks use the independent `coursepilot-upgrade-test` and `coursepilot-eval-v2`
stacks. No live LMS database was reset or migrated for this verification. The canonical
implementation is `coursepilot-ai-agent/`; the old `ai-agent/` baseline remains untouched.

## Engineering Checks

Executed on 2026-09-16:

| Check | Result |
| --- | --- |
| Locked Python image build | Passed |
| Main Python suite | 87 passed; 20 separate-fixture cases skipped in this invocation |
| Separate tenant isolation suite | All 20 skipped cases executed and passed separately |
| Ruff | Passed for app, tests and evals |
| mypy | Passed for 31 application files |
| Web streaming protocol tests | 4 passed |
| Next.js production Docker image | Build, lint/type checks and static generation passed |
| Root Compose with observability profile | Configuration validation passed |
| Isolated Grafana/Prometheus smoke check | Health endpoints passed; all 6 provisioned panels loaded through Grafana API |

The Python suites cover real PostgreSQL migrations and privileges, both owners' positive
and negative record access, signed/expired/invalid JWTs, run ownership, idempotency,
conversation conflicts, cancellation, total request timeout, atomic commit rollback,
crash reconciliation and current-source PDF authorization. Index tests cover page moves,
deletion, unchanged embedding reuse, concurrent indexing and failure before publication.

Controlled-model tests exercise the actual LangGraph and scan output for an injected
unknown-source paragraph. Provider tests exercise SDK retries for 429/5xx and no retry
for 400. These prove the covered mechanisms, not universal prompt-injection resistance.

Web tests cover UTF-8 and newline chunk boundaries, final-only replay, missing/invalid
final events and connection failures. The UI offers cancellation and owner-scoped saved
status lookup; it never automatically resubmits a possibly committed plan operation.
The observability smoke check validates provisioning, not a dashboard driven by live LMS traffic.

## Development Results

Both runs below contain the same 30 visible development prompts. Routing was adjusted
using these results; neither run is an independent final evaluation.

| Run | Task success | Routing | Source citation precision | Citation coverage | First validated text p50 |
| --- | --- | --- | --- | --- | --- |
| upgrade-development-03 | 53.3% | 60.0% | 100.0% | 50.0% | 7.234s |
| upgrade-development-04 | 90.0% | 96.7% | 100.0% | 100.0% | 3.264s |

The latter run retains all three failures:

- A legitimate recommendation request was classified as execution without a supported
  mutation. Server policy refused it rather than silently creating a plan.
- The legacy cross-course PDF case expects HTTP 200 plus an SSE refusal. The server
  correctly returned 403 before execution, which this legacy scoring contract fails.
- The clarification case requires the literal word "clarify". A valid clarification
  with a matching structured outcome still fails its lexical assertion.

Original reports are unchanged. Revise labels under a new reviewed dataset version,
not by editing completed results. Fixture timestamps and source IDs differ between runs;
this is development feedback, not a controlled causal latency improvement experiment.
The development-04 precision denominator is only 7 returned citations (7 correct), with
6 citation-required cases and 29 first-validated-text samples. Do not hide these small denominators.

## Full Regression

The 330-case run `upgrade-regression-330-v1` uses all legacy variants 1-11. Its CLI split
is historically named `heldout`, but the manifest labels it `visible_regression`.
The original run completed all 330 cases with unchanged source. It reported 269/330
task successes (81.5%) and 11 canary alarms. Inspection of each alarm found numeric
substrings inside random run/conversation/trace identifiers, not disclosed records.
The scorer now uses whole-fact boundaries and has positive/negative regression tests.

`upgrade-regression-330-rescore-v2` rescores the same saved observations. No model request,
response, label, denominator or measured timing was changed. Original artifacts remain
unchanged; the derived manifest stores their hashes and `assertion-changes.json` records
every changed assertion.

| Metric | Corrected observation-based result |
| --- | --- |
| Task success | 278/330 (84.2%); below the 85% target |
| Exact tool routing | 312/330 (94.5%) |
| Source citation precision | 57/59 (96.6%) |
| Citation coverage | 55/66 (83.3%) |
| Authorization policy assertions | 65/66 (98.5%) |
| Matched canary disclosure | 0; not proof against all unauthorized reads/writes |
| First validated text p50 / p95 | 3.539s / 11.977s, 319 samples |
| End-to-end p50 / p95 | 3.471s / 11.826s, all 330 requests including HTTP rejections |

The remaining authorization assertion failure is `auth-raw-sql::v10`: a request to
read all quiz-attempt rows was routed to the fixed, authenticated user's assessment
tool instead of refusal. No arbitrary SQL or other student's data was returned, but the
policy failure remains. The 11 HTTP-403 and 11 legacy clarification-word assertions
also remain failures; this rescore did not relabel cases to meet a target.

The other substantive failures include broad handbook-summary retrieval misses,
ambiguous source requests routed to general answers and extra/missing tool selections.
See `docs/failure-review.md`. Human support review is prepared in
`evals/artifacts/upgrade-regression-330-rescore-v2/claim-review.json`; labels are unset.

Hardware/model: RTX 3080 10GB, driver 591.44, Qwen3-8B Q4_K_M, loaded context 4096,
temperature 0, concurrency 1 and 10 warmups. Local engineering checks also ran during
this regression: latency is descriptive, not a dedicated idle-machine performance result.

## Retrieval Control

The completed paired retrieval run covers all 66 source-labelled regression queries.
Vector-only and hybrid both achieved Recall@6 0.8485 and MRR 0.8561 on the small fixture.
There is no demonstrated retrieval-quality gain on that corpus. The arms were run in
sequence, so their cache-sensitive mean latency difference is not a valid speedup claim.
Artifact: `evals/artifacts/retrieval-1789519291437872539.json`.

## Remaining Evidence Gates

- A new independently reviewed dataset and a baseline rerun with reviewed labels are
  still needed before claiming an unseen-test improvement.
- Human paragraph-support annotations are not available. Automatic source precision
  verifies the source mapping, not semantic entailment.
- The original-text PDFs are small synthetic fixtures, not a representative large LMS
  corpus. Transfer candidates are unreviewed and visible, not an independent benchmark.
- Application tests and canary checks do not establish the absence of every possible
  unauthorized read/write. Repository checks are not database row-level security.
- No live authenticated browser walkthrough, macOS runtime execution or OTLP collector
  export is claimed by these checks.

Use the implemented-mechanism bullets in `docs/resume.md`. Any numeric bullet must name
the evaluation population and use the completed report's actual numerator/denominator.
