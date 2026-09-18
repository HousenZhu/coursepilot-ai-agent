"""Isolated eval-only fault injection; never imported by the production entry point."""
from contextvars import ContextVar
from urllib.parse import urlsplit

from app.config import get_settings
from app.main import app as production_app
from app.repositories.lms import LMSRepository

if urlsplit(get_settings().database_url).path != "/coursepilot_eval":
    raise RuntimeError("Fault injection is restricted to coursepilot_eval")

FAULTS = {"assessment_timeout", "deadline_failure", "profile_failure", "assessment_failure", "snapshot_failure"}
current_fault: ContextVar[dict | None] = ContextVar("eval_fault", default=None)


def install_fault(method_name: str, kinds: set[str]) -> None:
    original = getattr(LMSRepository, method_name)
    async def read(self, *args, **kwargs):
        state = current_fault.get()
        if state:
            state[method_name] = state.get(method_name, 0) + 1
            # The initial profile read establishes trusted policy scope, not tool evidence.
            policy_read = method_name == "get_student_profile" and state[method_name] == 1
            if state["kind"] in kinds and not policy_read:
                raise TimeoutError("Injected evaluation dependency failure")
        return await original(self, *args, **kwargs)
    setattr(LMSRepository, method_name, read)


install_fault("get_assessment_performance", {"assessment_timeout", "assessment_failure", "snapshot_failure"})
install_fault("get_upcoming_deadlines", {"deadline_failure", "snapshot_failure"})
install_fault("get_student_profile", {"profile_failure", "snapshot_failure"})


class FaultApplication:
    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        fault = headers.get(b"x-eval-fault", b"").decode("ascii", errors="ignore")
        token = current_fault.set({"kind": fault} if fault in FAULTS else None)
        try:
            await production_app(scope, receive, send)
        finally:
            current_fault.reset(token)


app = FaultApplication()
