# CoursePilot Evaluation: upgrade-development-03

## Summary

- Split: `visible_regression`
- Cases: 30
- Task success: 53.3%
- Tool routing accuracy: 60.0%
- Grounding correctness: 73.3%
- Citation precision: 100.0%
- Citation coverage: 50.0%
- Authorization pass rate: 100.0%
- Authorization leaks: 0
- Error rate: 6.7%
- Median first verified text: 7.234s
- p95 first verified text: 19.177s
- Median end-to-end latency: 7.054s
- p95 end-to-end latency: 18.238s

## Category Success

- authorization: 83.3%
- citation: 40.0%
- clarification: 0.0%
- conversation-history: 100.0%
- direct-answer: 100.0%
- grounding: 33.3%
- multi-tool: 66.7%
- tool-routing: 33.3%

## Reproducibility

- Dataset SHA-256: `0086bdd9aad72c965f7fa3a0b60a9d39ce133ee885f78edd858cbb86dddad123`
- Git commit: `working-tree`
- Model: `qwen3:8b`
- Temperature: `0.0`
- Concurrency: `1`
- Warm-up requests: `10`

## Failed Cases

- `route-profile::v00`
- `route-assessment::v00`
- `route-create-plan::v00`
- `route-active-plan::v00`
- `ground-progress::v00`
- `ground-low-quiz::v00`
- `ground-average::v00`
- `ground-assignment::v00`
- `cite-css::v00`
- `cite-flexbox::v00`
- `cite-event-loop::v00`
- `auth-other-material::v00`
- `clarify-ambiguous::v00`
- `multi-diagnosis-plan::v00`
