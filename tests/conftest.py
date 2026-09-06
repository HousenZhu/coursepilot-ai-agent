import os


os.environ.setdefault("AGENT_INTERNAL_SECRET", "test-secret-that-is-at-least-32-characters")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://coursepilot:coursepilot@localhost:5432/learning_platform",
)
os.environ.setdefault(
    "CHECKPOINT_DATABASE_URL",
    "postgresql://coursepilot:coursepilot@localhost:5432/learning_platform",
)
