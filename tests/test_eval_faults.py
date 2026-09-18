import asyncio
import importlib
import sys

import pytest

from app.config import get_settings
from app.repositories.lms import LMSRepository


def test_fault_entrypoint_rejects_non_eval_database():
    assert "coursepilot_eval" not in get_settings().database_url
    with pytest.raises(RuntimeError, match="restricted to coursepilot_eval"):
        importlib.import_module("evals.fault_app")


async def test_injected_fault_is_request_local_and_preserves_policy_scope(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql+asyncpg://unused@localhost/coursepilot_eval")
    async def safe_read(self, *args, **kwargs):
        return {"verified": True}
    for name in ("get_student_profile", "get_assessment_performance", "get_upcoming_deadlines"):
        monkeypatch.setattr(LMSRepository, name, safe_read)
    module = importlib.import_module("evals.fault_app")
    repo = LMSRepository()
    async def failing_request():
        token = module.current_fault.set({"kind": "snapshot_failure"})
        try:
            assert await repo.get_student_profile("owner") == {"verified": True}
            await asyncio.sleep(0)
            with pytest.raises(TimeoutError):
                await repo.get_student_profile("owner")
            with pytest.raises(TimeoutError):
                await repo.get_assessment_performance("owner")
        finally:
            module.current_fault.reset(token)
    async def ordinary_request():
        assert await repo.get_assessment_performance("owner") == {"verified": True}
    try:
        await asyncio.gather(failing_request(), ordinary_request())
        assert module.current_fault.get() is None
    finally:
        sys.modules.pop("evals.fault_app", None)
