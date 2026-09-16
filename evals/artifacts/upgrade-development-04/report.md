# CoursePilot Evaluation: upgrade-development-04

## Summary

- Split: `visible_regression`
- Cases: 30
- Task success: 90.0%
- Tool routing accuracy: 96.7%
- Grounding correctness: 93.3%
- Citation precision: 100.0%
- Citation coverage: 100.0%
- Authorization pass rate: 100.0%
- Authorization leaks: 0
- Error rate: 3.3%
- Median first verified text: 3.264s
- p95 first verified text: 7.344s
- Median end-to-end latency: 3.270s
- p95 end-to-end latency: 7.280s

## Category Success

- authorization: 83.3%
- citation: 100.0%
- clarification: 0.0%
- conversation-history: 100.0%
- direct-answer: 100.0%
- grounding: 83.3%
- multi-tool: 100.0%
- tool-routing: 100.0%

## Reproducibility

- Dataset SHA-256: `0086bdd9aad72c965f7fa3a0b60a9d39ce133ee885f78edd858cbb86dddad123`
- Git commit: `working-tree`
- Model: `qwen3:8b`
- Temperature: `0.0`
- Concurrency: `1`
- Warm-up requests: `10`

## Failed Cases

- `ground-recommendation::v00`
- `auth-other-material::v00`
- `clarify-ambiguous::v00`
