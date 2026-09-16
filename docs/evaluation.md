# Evaluation Protocol

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

- Exact tool-set matching measures routing; supported alternative valid routes must be
  reviewed as labels rather than silently added after seeing results.
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

## Controlled experiments

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
