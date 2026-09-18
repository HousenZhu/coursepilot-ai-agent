# Evaluation Protocol

## ReAct candidate status

The current ReAct candidate set has 180 cases: five deterministically selected variants
from each of the 30 visible legacy templates (150), plus 30 new scenarios. Selection
uses a fixed seed and case-ID hash, independent of observed pass rates. The new scenarios
cover tool feedback, parallel record reads, evidence synthesis, plans, authorization and
fault recovery. Their labels remain pending human review. No 180-case run has been made
and no new ReAct task success, citation or latency score is available.

The default ReAct runner's `heldout` CLI split selects these visible candidates. The
name is inherited from the legacy runner; it does not confer unseen-test status.
`development` selects the original 30 development variants. The earlier 330-case report
measures deterministic dispatch, so its metrics cannot be assigned to ReAct.

## Separate populations

The legacy 30 templates (development variant 0; 330 other variants) are all visible
regression data. A split name does not make previously inspected examples unseen.
The historical report used a different scorer and stream definition; percentage or
latency changes against it are not controlled improvement measurements.

The 16 transfer candidates use different course facts and original PDF text. They are
review material, not an independently authored final dataset. Set the fixture profile
to transfer-v1 before creating a separate eval stack. Do not switch a running stack.
Inspect labels, expected tools, fact tuples and evidence anchors. Commission additional
unseen cases for a final evaluation; do not tune against their outcomes.

An external JSON case list can be passed with --cases. A reviewer must supply a JSON
approval with dataset_sha256 (from evals.dataset.dataset_sha256), reviewer, and approved=true
before --review labels the run reviewed_final. The software records approval, not proof
of independence; the reviewer remains responsible for that statement.

## Scoring

- ReAct routing checks that required capabilities completed and forbidden tools were
  not called. It records the actual tool chain, call count and whether each started
  call completed or failed. Equivalent valid trajectories can satisfy the same need.
  Legacy reports retain their exact tool-set score under the old scorer.
- An HTTP 403/404 can satisfy a case explicitly labeled for refusal if no tool,
  citation or private data leaks. Label changes are recorded separately from runs.
- required_fact_tuples compare subject, entity, metric, value and unit. Legacy required_facts
  still use boundary-aware lexical matching and are explicitly a weaker regression check.
- Source precision counts every returned citation. A source is matched by content ID,
  page and anchor. Any incorrect citation fails task success, even if coverage passes.
- Recall@6 and MRR are retrieval metrics; they do not measure answer quality.
- Canary scanning includes all streamed text and the final answer, plan and citations.
  An unexpected tool call is a policy assertion failure, not automatically a data leak.
  Unauthorized reads/writes require independent repository/audit evidence; absence of an
  audit flag does not establish they never occurred.
- Human claim-support labels are not fabricated or replaced with lexical source matching.
  Record unsupported and contradictory claims as well as supported claims. Until reviewed,
  claim-support rate is not available.
- Provider errors, missing final, invalid final schema and outcome mismatches fail the case.
  Refusals that emit text are included in first-validated-text timing. Report denominators.
- Plan cases check that a plan was saved and the requested horizon, per-day tasks and
  evidence are valid. Recovery cases require an observed failed snapshot section and
  no saved plan. Report plan validity and loop completion separately.

## Controlled experiments

Five recovery candidates use `evals.fault_app` in the isolated evaluation stack. Its
database-name guard requires `coursepilot_eval`; fault headers are not available in the
production entrypoint. A normal successful request does not exercise these cases.

Re-run the baseline with the same corrected scorer and fixture before comparing variants.
Change only one factor: vector-only versus hybrid retrieval, embedding reuse, or orchestration.
Do not reset data on resume. The journal preserves interrupted attempts as failures.
Plan fixtures are reset between cases only after all earlier requests have terminated.
Use all prescribed cases, including failures; report incomplete runs as incomplete.

Model name/digest, actual running context, temperature, warmups, dependency versions,
source snapshot and fixture hashes accompany results. The UI-visible segment latency
cannot be directly compared with raw provider token latency.

## Scorer corrections

Use `python -m evals.rescore ORIGINAL_DIR NEW_DIR --reason "..."` to apply a documented
scorer correction to complete saved observations. This makes no model calls, refuses to
overwrite an output directory and retains every original case and timing. Input hashes,
the new scorer snapshot and an assertion-level change log distinguish it from a new run.
Never present rescoring as a runtime quality or latency improvement. Label changes still
require a separate reviewed dataset version; this command does not change labels.

## Review packet

Include failure JSON, relevant redacted trace, expected evidence and the observed response.
Classify failures as route, scope, missing evidence, retrieval, source mismatch, semantic
support, transport, or state consistency. Do not change a frozen final set to fit output.
