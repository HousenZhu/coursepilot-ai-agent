# Upstream LMS Relationship

CoursePilot was built as a backend extension to the team LearnHub LMS project:

- Upstream project: [personalized-learning-platform](https://github.com/HousenZhu/personalized-learning-platform)
- Upstream responsibilities: Next.js UI, Better Auth session management, LMS data ownership, course upload workflows, and the PostgreSQL public schema.
- CoursePilot responsibilities: authenticated Agent orchestration, scoped data tools, document retrieval, Agent-owned persistence in the `agent` schema, streaming, observability, and evals.

The LMS remains authoritative for all student records. CoursePilot reads public LMS tables via fixed queries and should use a database account that has `SELECT` access only. It writes its own conversations, plans, runs, document chunks, and checkpoints only in the `agent` schema.

## Required LMS Tables

The integration expects the upstream LMS to expose `users`, `courses`, `enrollments`, `modules`, `contents`, `quizzes`, `quiz_attempts`, `assignments`, and `submissions`. The exact schema mapping is implemented in `app/repositories/lms.py`.

## BFF Boundary

The Next.js BFF must validate the Better Auth session and sign a short-lived JWT for the Agent. The Agent request body intentionally does not include a `user_id`; the verified JWT subject is the only identity source passed into tools. This prevents a browser or model from selecting a different student's data.

## Attribution

This repository is a standalone portfolio/service extraction. It documents its dependency on the original team LMS and does not claim ownership of the upstream platform or copy its UI.
