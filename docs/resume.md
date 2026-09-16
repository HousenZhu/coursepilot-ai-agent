# Portfolio and Resume Material

## Attribution

CoursePilot is the individually implemented Python Agent extension to the team LearnHub
LMS. Do not claim authorship of the upstream Next.js platform or all LMS functionality.

## English bullets

**CoursePilot - Evidence-backed Learning Agent Backend**
*Python, FastAPI, LangGraph, PostgreSQL/pgvector, SQLAlchemy, OpenTelemetry, Docker*

- Built an asynchronous AI backend integrated with a Next.js LMS, using authenticated
  tool dispatch, database-backed request idempotency and transactional persistence of
  conversation results and study plans.
- Implemented enrollment-filtered hybrid retrieval over course PDFs, combining pgvector
  and PostgreSQL full-text search with versioned citations and content-addressed embedding reuse.
- Added failure-injection and authorization tests plus a reproducible evaluation harness
  recording source, fixture and model fingerprints, complete attempt history and
  first-validated-segment latency.

These describe implemented mechanisms, not universal reliability or measured model quality.
Add quantitative bullets only from a completed, valid report. Identify visible regression,
reviewed transfer evaluation and hardware explicitly; do not call repeated development cases held-out.
Source citation precision and human-reviewed claim support are different measurements.

## Chinese bullets

**CoursePilot - 基于证据的个性化学习 Agent 后端**

- 在团队 LearnHub LMS 基础上独立实现 Python AI 后端，通过 FastAPI、LangGraph 和 SSE
  集成授权工具调度、数据库幂等控制及学习计划与最终回答的事务性保存。
- 构建按选课权限过滤的 PDF 混合检索，结合 pgvector、PostgreSQL 全文检索与 RRF，
  实现版本化引用、增量索引和未变化内容的 embedding 复用。
- 建立权限隔离、故障注入与可复现评测流程，保存源码、数据和模型指纹，
  分别衡量工具路由、来源引用正确性与首个已验证文本段延迟。

以上描述个人 Agent 模块贡献，不等同于整个团队 LMS 的作者归属。
真实量化结果必须标注评测集性质；可见回归集不得包装成独立未见测试集。
