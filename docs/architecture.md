# Architecture and Engineering Decisions

## Request path

1. A signed-in browser sends a message to the Next.js `/api/chatbot` BFF.
2. Next.js verifies the Better Auth session and signs a 60-second internal JWT.
3. FastAPI validates the JWT and creates an immutable `AuthContext`.
4. A structured LLM router emits a validated processing mode, subject, risk flags, and a
   multi-label capability set. Invalid or uncertain routes fail closed to clarification.
5. Server policy converts capabilities to an allowlist of request-scoped tools. No tool
   exposes `user_id` to the LLM.
6. A planner calls only allowlisted tools until every required capability has successful
   current-turn evidence or the bounded loop ends.
7. The final answer node runs only after evidence is ready. Only this node streams tokens;
   planner drafts are never sent to the browser.
8. A deterministic verifier matches tool-result kinds against routed capabilities.
9. Checkpoints, display messages, study plans, and run metadata are persisted separately.

## Routing model

The six processing modes are `direct_answer`, `conversation_answer`,
`retrieve_then_answer`, `execute_then_answer`, `clarify`, and `refuse`. Capabilities are
independent and composable: student profile, assessment records, deadlines, course material,
active plan, and plan mutation. This avoids forcing a request such as “use my grades and
deadlines” into one flat intent.

The router is not an authorization boundary. Its Pydantic output is normalized by server
policy, other-user and unsupported mutation routes are rejected, tool names are mapped from
server-owned enums, and repositories still enforce the authenticated identity and enrollment.
Conversation history can support a statement about the chat, but never counts as evidence of
an LMS fact.

## Data ownership

Prisma continues to own the public LMS schema. Alembic owns only the `agent` schema. Python
reads LMS tables through fixed SQLAlchemy statements because duplicating Prisma's schema in a
second ORM would create migration ownership ambiguity.

Checkpoints are runtime state. `messages` are product display/audit history. Keeping both is
intentional: checkpoint serialization may evolve with LangGraph, while the product history
contract remains stable.

## RAG decisions

- Scope retrieval by enrollment before vector ranking; filtering after retrieval could leak
  another course's text into model context.
- Store content ID, title, page, excerpt, and hash alongside every vector.
- Re-index by deleting and replacing one content item's chunks in a transaction.
- Use exact cosine search for the initial corpus. Approximate HNSW adds tuning and recall
  tradeoffs that are unjustified without scale measurements.
- Treat retrieved text as untrusted. Document instructions never override the system policy.

## Failure handling

- Invalid or expired BFF tokens return 401 before Agent execution.
- Unknown request fields, including `user_id`, return 422.
- Provider calls time out after 25 seconds and retry transient failures at most twice.
- Router failures and ambiguous data routes become a clarification instead of guessing.
- The graph allows at most four model/tool rounds.
- Tool results containing an error do not satisfy evidence requirements.
- Client disconnects cancel the stream and mark the run failed.
- Error events expose a trace ID, not internal exceptions or prompts.

## Scale path

The first scale step is additional Agent replicas because API and graph construction are
stateless outside PostgreSQL. If vector corpus size makes exact search slow, add HNSW and
measure recall. If ingestion blocks API workers, move only ingestion to a queue; conversational
requests should remain synchronous and bounded.
