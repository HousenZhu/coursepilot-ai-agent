"""Append-only attempts and content-addressed, secret-free experiment snapshots."""
import hashlib
import importlib.metadata
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def journal(path: Path, event: str, **fields: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), "event": event, **fields}, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def snapshot_source(root: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(destination, "w") as archive:
        files = [path for folder in ("app", "evals", "alembic") for path in (root / folder).rglob("*")
                 if path.is_file() and path.suffix in {".py", ".jsonl"}
                 and not {"artifacts", "__pycache__"}.intersection(path.parts)]
        files += [path for name in ("pyproject.toml", "uv.lock") if (path := root / name).is_file()]
        for path in sorted(files):
            name, data = path.relative_to(root).as_posix(), path.read_bytes()
            digest.update(name.encode() + b"\0" + data + b"\0")
            archive.writestr(name, data)
    return digest.hexdigest()


def dependency_versions() -> dict[str, str]:
    return dict(sorted((dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions()
                       if dist.metadata.get("Name")))


async def fixture_snapshot() -> dict[str, Any]:
    from sqlalchemy import text
    from app.db import SessionFactory
    result = {}
    async with SessionFactory() as session:
        database = await session.scalar(text("SELECT current_database()"))
        if database not in {"coursepilot_eval", "coursepilot_upgrade_test"}:
            raise RuntimeError("Evaluation may only inspect an isolated fixture database")
        for table in ("users", "courses", "enrollments", "contents", "quizzes", "quiz_attempts", "assignments", "submissions"):
            result[table] = [dict(row) for row in (await session.execute(text(f"SELECT * FROM {table} ORDER BY id"))).mappings()]
        result["documents"] = [dict(row) for row in (await session.execute(text(
            "SELECT content_id, file_hash, pipeline_version, active_version FROM agent.document_versions ORDER BY content_id"))).mappings()]
        result["baseline_plan_ids"] = ["50000000-0000-0000-0000-000000000005", "60000000-0000-0000-0000-000000000006"]
    return result


async def reset_case_plans() -> None:
    from sqlalchemy import text
    from app.db import SessionFactory
    async with SessionFactory() as session, session.begin():
        database = await session.scalar(text("SELECT current_database()"))
        if database not in {"coursepilot_eval", "coursepilot_upgrade_test"}:
            raise RuntimeError("Case reset is isolated-fixture-only")
        # Never reset mutable fixtures while a timed-out request may still own them.
        active = await session.scalar(text("SELECT count(*) FROM agent.agent_runs WHERE status = 'running'"))
        if active:
            raise RuntimeError("An unfinished request exists; inspect it before continuing evaluation")
        await session.execute(text("""DELETE FROM agent.study_plans WHERE id NOT IN
            ('50000000-0000-0000-0000-000000000005', '60000000-0000-0000-0000-000000000006')"""))
        await session.execute(text("UPDATE agent.study_plans SET status = 'active'"))
