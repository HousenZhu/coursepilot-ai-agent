# Portfolio and Resume Material

## Attribution

CoursePilot is the individually implemented Python Agent extension to the team LearnHub
LMS. Do not claim authorship of the upstream Next.js platform or all LMS functionality.

## English bullets

**CoursePilot - Evidence-backed Learning Agent Backend**
*Python, FastAPI, LangGraph, PostgreSQL/pgvector, SQLAlchemy, OpenTelemetry, Docker*

- Built an asynchronous AI backend integrated with a Next.js LMS, using a bounded
  LangGraph ReAct loop in which Qwen3 selects permission-scoped tools, observes results,
  and synthesizes evidence-based learning advice; persisted validated plans atomically
  with conversation results under database-backed request idempotency.
- Implemented enrollment-filtered hybrid retrieval over course PDFs, combining pgvector
  and PostgreSQL full-text search with versioned citations and content-addressed embedding reuse.
- Added authorization and controlled failure tests plus a reproducible evaluation harness
  recording source, fixture and model fingerprints, complete attempt history and
  first-validated-segment latency. The 180 ReAct candidates remain unrun and unreviewed.

These describe implemented mechanisms, not universal reliability or measured model quality.
Add quantitative bullets only from a completed, valid report. Identify visible regression,
reviewed transfer evaluation and hardware explicitly; do not call repeated development cases held-out.
Source citation precision and human-reviewed claim support are different measurements.

## Chinese bullets

**CoursePilot - 基于证据的个性化学习 Agent 后端**

- 在团队 LearnHub LMS 基础上独立实现 Python AI 后端，通过 FastAPI 与 LangGraph
  构建有界 ReAct 工具循环，让 Qwen3 选择权限受控工具、读取结果并生成证据化学习建议；
  使用 SSE 输出验证后的回答，并以数据库幂等控制和事务保存学习计划。
- 构建按选课权限过滤的 PDF 混合检索，结合 pgvector、PostgreSQL 全文检索与 RRF，
  实现版本化引用、增量索引和未变化内容的 embedding 复用。
- 建立权限隔离、故障注入与可复现评测流程，保存源码、数据和模型指纹，
  分别衡量工具路由、来源引用正确性与首个已验证文本段延迟。

以上描述个人 Agent 模块贡献，不等同于整个团队 LMS 的作者归属。
真实量化结果必须标注评测集性质；可见回归集不得包装成独立未见测试集。
