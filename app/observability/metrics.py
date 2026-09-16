from prometheus_client import Counter, Histogram

LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 180, 300, 600)
STAGE_LATENCY = Histogram("coursepilot_stage_duration_seconds", "Stage duration", ["stage"], buckets=LATENCY_BUCKETS)


AGENT_RUNS = Counter(
    "coursepilot_agent_runs_total",
    "Agent runs by outcome",
    ["status"],
)
AGENT_LATENCY = Histogram(
    "coursepilot_agent_run_duration_seconds",
    "End-to-end agent run latency",
    buckets=LATENCY_BUCKETS,
)
AGENT_TTFT = Histogram(
    "coursepilot_agent_ttft_seconds",
    "Time from agent request start to first streamed answer token",
    buckets=LATENCY_BUCKETS,
)
TOOL_CALLS = Counter(
    "coursepilot_tool_calls_total",
    "Tool calls by tool and outcome",
    ["tool", "status"],
)
RETRIEVAL_RESULTS = Histogram(
    "coursepilot_retrieval_results",
    "Number of chunks returned by retrieval",
    buckets=(0, 1, 2, 3, 4, 6, 8, 12),
)
