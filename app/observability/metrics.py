from prometheus_client import Counter, Histogram


AGENT_RUNS = Counter(
    "coursepilot_agent_runs_total",
    "Agent runs by outcome",
    ["status"],
)
AGENT_LATENCY = Histogram(
    "coursepilot_agent_run_duration_seconds",
    "End-to-end agent run latency",
)
AGENT_TTFT = Histogram(
    "coursepilot_agent_ttft_seconds",
    "Time from agent request start to first streamed answer token",
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
