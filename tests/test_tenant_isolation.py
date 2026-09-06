import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import engine
from app.repositories.lms import LMSRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 against an isolated PostgreSQL test database",
)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_lms_records() -> None:
    async with engine.begin() as connection:
        statements = [
            """CREATE TABLE IF NOT EXISTS users (
                  id text PRIMARY KEY, name text NOT NULL, role text NOT NULL
                )""",
            """CREATE TABLE IF NOT EXISTS courses (
                  id text PRIMARY KEY, title text NOT NULL, "teacherId" text NOT NULL
                )""",
            """CREATE TABLE IF NOT EXISTS enrollments (
                  id text PRIMARY KEY, "studentId" text NOT NULL, "courseId" text NOT NULL,
                  progress double precision NOT NULL DEFAULT 0,
                  completed boolean NOT NULL DEFAULT false,
                  "enrolledAt" timestamptz NOT NULL DEFAULT now()
                )""",
            """CREATE TABLE IF NOT EXISTS modules (
                  id text PRIMARY KEY, title text NOT NULL, "order" integer NOT NULL,
                  "courseId" text NOT NULL
                )""",
            """CREATE TABLE IF NOT EXISTS assignments (
                  id text PRIMARY KEY, title text NOT NULL, deadline timestamptz NOT NULL,
                  "moduleId" text NOT NULL, "maxScore" integer NOT NULL DEFAULT 100
                )""",
            """CREATE TABLE IF NOT EXISTS submissions (
                  id text PRIMARY KEY, "assignmentId" text NOT NULL, "studentId" text NOT NULL,
                  grade double precision, status text NOT NULL DEFAULT 'SUBMITTED',
                  "submittedAt" timestamptz NOT NULL DEFAULT now()
                )""",
        ]
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(
            text("TRUNCATE submissions, assignments, modules, enrollments, courses, users")
        )
        for index in range(20):
            await connection.execute(
                text(
                    "INSERT INTO users (id, name, role) "
                    "VALUES (:student_id, :student_name, 'STUDENT')"
                ),
                {
                    "student_id": f"student-{index}",
                    "student_name": f"Student {index}",
                },
            )
            await connection.execute(
                text(
                    'INSERT INTO courses (id, title, "teacherId") '
                    "VALUES (:course_id, :course_title, 'teacher-1')"
                ),
                {
                    "course_id": f"course-{index}",
                    "course_title": f"Private Course {index}",
                },
            )
            await connection.execute(
                text(
                    'INSERT INTO enrollments (id, "studentId", "courseId", progress, completed) '
                    "VALUES (:enrollment_id, :student_id, :course_id, :progress, false)"
                ),
                {
                    "enrollment_id": f"enrollment-{index}",
                    "student_id": f"student-{index}",
                    "course_id": f"course-{index}",
                    "progress": index * 5,
                },
            )
            await connection.execute(
                text(
                    'INSERT INTO modules (id, title, "order", "courseId") '
                    "VALUES (:module_id, :module_title, 1, :course_id)"
                ),
                {
                    "module_id": f"module-{index}",
                    "module_title": f"Module {index}",
                    "course_id": f"course-{index}",
                },
            )
            await connection.execute(
                text(
                    'INSERT INTO assignments (id, title, deadline, "moduleId", "maxScore") '
                    "VALUES (:assignment_id, :assignment_title, "
                    "now() + interval '2 days', :module_id, 100)"
                ),
                {
                    "assignment_id": f"assignment-{index}",
                    "assignment_title": f"Private Assignment {index}",
                    "module_id": f"module-{index}",
                },
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("student_index", range(20))
async def test_deadlines_are_scoped_to_authenticated_student(student_index: int) -> None:
    rows = await LMSRepository().get_upcoming_deadlines(f"student-{student_index}", days=14)
    assert len(rows) == 1
    assert rows[0]["course_id"] == f"course-{student_index}"
    assert rows[0]["title"] == f"Private Assignment {student_index}"
