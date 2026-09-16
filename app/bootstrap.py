"""One-shot migration-role grants. Never executed by the runtime account."""
import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    dsn = (settings.migration_database_url or settings.database_url).replace("postgresql+asyncpg://", "postgresql://")
    role = "coursepilot_agent"
    password = os.environ["AGENT_DB_PASSWORD"]
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS agent")
        connection.execute("SET search_path TO agent, public")
        PostgresSaver(connection).setup()
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
        connection.execute(sql.SQL("ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public, agent TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
        for table in ("users", "courses", "enrollments", "modules", "contents", "quizzes", "quiz_attempts", "assignments", "submissions"):
            found = connection.execute("SELECT to_regclass(%s) AS name", (f"public.{table}",)).fetchone()
            if found and found["name"]:
                privilege = sql.SQL("SELECT (id, name, role)") if table == "users" else sql.SQL("SELECT")
                connection.execute(sql.SQL("GRANT {} ON public.{} TO {}").format(privilege, sql.Identifier(table), sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agent TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA agent GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    main()
