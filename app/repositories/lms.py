from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db import SessionFactory


class LMSRepository:
    async def get_student_profile(self, user_id: str) -> dict[str, Any]:
        query = text(
            """
            SELECT u.id, u.name, u.role,
                   COALESCE(
                     json_agg(
                       json_build_object(
                         'course_id', c.id,
                         'title', c.title,
                         'progress', e.progress,
                         'completed', e.completed
                       ) ORDER BY e."enrolledAt" DESC
                     ) FILTER (WHERE c.id IS NOT NULL),
                     '[]'::json
                   ) AS courses
            FROM users u
            LEFT JOIN enrollments e ON e."studentId" = u.id
            LEFT JOIN courses c ON c.id = e."courseId"
            WHERE u.id = :user_id AND u.role = 'STUDENT'
            GROUP BY u.id, u.name, u.role
            """
        )
        async with SessionFactory() as session:
            row = (await session.execute(query, {"user_id": user_id})).mappings().one_or_none()
        if row is None:
            return {"found": False, "courses": []}
        return {"found": True, **dict(row)}

    async def get_assessment_performance(
        self, user_id: str, course_id: str | None = None
    ) -> dict[str, Any]:
        quiz_query = text(
            """
            SELECT c.id AS course_id, c.title AS course_title,
                   q.id AS quiz_id, q.title AS quiz_title,
                   qa.score, qa.passed, qa."submittedAt" AS submitted_at
            FROM quiz_attempts qa
            JOIN quizzes q ON q.id = qa."quizId"
            JOIN modules m ON m.id = q."moduleId"
            JOIN courses c ON c.id = m."courseId"
            JOIN enrollments e ON e."courseId" = c.id AND e."studentId" = :user_id
            WHERE qa."studentId" = :user_id
              AND qa."submittedAt" IS NOT NULL
              AND (CAST(:course_id AS TEXT) IS NULL OR c.id = CAST(:course_id AS TEXT))
            ORDER BY qa."submittedAt" DESC
            LIMIT 10
            """
        )
        assignment_query = text(
            """
            SELECT c.id AS course_id, c.title AS course_title,
                   a.id AS assignment_id, a.title AS assignment_title,
                   s.grade, a."maxScore" AS max_score, s.status,
                   s."submittedAt" AS submitted_at
            FROM submissions s
            JOIN assignments a ON a.id = s."assignmentId"
            JOIN modules m ON m.id = a."moduleId"
            JOIN courses c ON c.id = m."courseId"
            JOIN enrollments e ON e."courseId" = c.id AND e."studentId" = :user_id
            WHERE s."studentId" = :user_id
              AND (CAST(:course_id AS TEXT) IS NULL OR c.id = CAST(:course_id AS TEXT))
            ORDER BY s."submittedAt" DESC
            LIMIT 10
            """
        )
        params = {"user_id": user_id, "course_id": course_id}
        async with SessionFactory() as session:
            quizzes = (await session.execute(quiz_query, params)).mappings().all()
            assignments = (await session.execute(assignment_query, params)).mappings().all()

        scored = [float(row["score"]) for row in quizzes if row["score"] is not None]
        average_quiz_score = round(sum(scored) / len(scored), 2) if scored else None
        return {
            "quiz_attempts": [self._serialize_row(row) for row in quizzes],
            "assignments": [self._serialize_row(row) for row in assignments],
            "average_quiz_score": average_quiz_score,
        }

    async def get_upcoming_deadlines(
        self,
        user_id: str,
        days: int = 14,
        course_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = text(
            """
            SELECT a.id AS assignment_id, a.title, a.deadline,
                   c.id AS course_id, c.title AS course_title,
                   s.status AS submission_status
            FROM assignments a
            JOIN modules m ON m.id = a."moduleId"
            JOIN courses c ON c.id = m."courseId"
            JOIN enrollments e ON e."courseId" = c.id AND e."studentId" = :user_id
            LEFT JOIN submissions s
              ON s."assignmentId" = a.id AND s."studentId" = :user_id
            WHERE a.deadline >= NOW()
              AND a.deadline <= NOW() + make_interval(days => :days)
              AND (CAST(:course_id AS TEXT) IS NULL OR c.id = CAST(:course_id AS TEXT))
            ORDER BY a.deadline ASC
            LIMIT 20
            """
        )
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    query,
                    {"user_id": user_id, "days": days, "course_id": course_id},
                )
            ).mappings().all()
        return [self._serialize_row(row) for row in rows]

    async def student_has_course(self, user_id: str, course_id: str) -> bool:
        query = text(
            'SELECT 1 FROM enrollments WHERE "studentId" = :user_id AND "courseId" = :course_id'
        )
        async with SessionFactory() as session:
            return (await session.execute(query, {"user_id": user_id, "course_id": course_id})).first() is not None

    async def teacher_owns_course(self, user_id: str, course_id: str) -> bool:
        query = text(
            'SELECT 1 FROM courses WHERE id = :course_id AND "teacherId" = :user_id'
        )
        async with SessionFactory() as session:
            return (await session.execute(query, {"user_id": user_id, "course_id": course_id})).first() is not None

    async def get_pdf_contents(self, course_id: str) -> list[dict[str, Any]]:
        query = text(
            """
            SELECT ct.id, ct.title, ct."fileUrl" AS file_url
            FROM contents ct
            JOIN modules m ON m.id = ct."moduleId"
            WHERE m."courseId" = :course_id
              AND ct.type = 'PDF'
              AND ct."fileUrl" IS NOT NULL
            ORDER BY m."order", ct."order"
            """
        )
        async with SessionFactory() as session:
            rows = (await session.execute(query, {"course_id": course_id})).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _serialize_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for key, value in result.items():
            if isinstance(value, datetime):
                result[key] = value.isoformat()
        return result
