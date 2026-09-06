# Resume Copy

Use these bullets after the implementation has been deployed and verified. Do not add target
metrics until `evals/results.json` contains the measured result.

## English

**CoursePilot - Production-Grade AI Learning Agent**
*Python, FastAPI, LangGraph, PostgreSQL/pgvector, SQLAlchemy, OpenTelemetry, Docker*

- Architected a standalone asynchronous AI agent service integrated with a Next.js learning
  platform, using bounded LangGraph tool orchestration and SSE streaming to deliver
  personalized, stateful study guidance.
- Built permission-scoped tools and a citation-backed RAG pipeline over course PDFs, combining
  PostgreSQL learning records with pgvector retrieval while preventing cross-user access and
  arbitrary model-generated SQL.
- Established typed API contracts, persistent checkpoints, bounded retries, distributed
  tracing, Prometheus metrics, and a 30-case evaluation suite covering tool routing,
  grounding, prompt injection, and authorization.

After measurement, optionally add:

`Achieved X% tool-routing accuracy and Y% citation coverage across a 30-case offline evaluation set.`

## Chinese

**CoursePilot - 生产级个性化学习 AI Agent**

- 基于 FastAPI 与 LangGraph 设计独立异步 Agent 服务，通过 SSE 与 Next.js LMS 集成，
  实现学习诊断、多轮记忆及个性化学习计划生成。
- 将课程进度、测验、作业和截止日期封装为强类型权限工具，并基于 PostgreSQL/pgvector
  构建带页码引用的课程 PDF RAG，确保检索结果按用户和课程隔离。
- 建立包含超时重试、持久化 checkpoint、结构化日志、Tracing、Metrics 和 30 条离线评测
  的工程体系，覆盖工具路由、事实 grounding、Prompt injection 与越权访问。
