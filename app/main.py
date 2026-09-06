from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api.routes import router
from app.config import get_settings
from app.db import dispose_engine
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing


settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncPostgresSaver.from_conn_string(
        settings.checkpoint_database_url
    ) as checkpointer:
        await checkpointer.setup()
        app.state.checkpointer = checkpointer
        yield
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Permission-scoped learning copilot agent service",
    lifespan=lifespan,
)
app.include_router(router)
configure_tracing(app, settings)
