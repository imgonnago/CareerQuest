"""표준 직무 목록 조회 (1단계 직무 설정 LLM이 직무명을 통일할 때 쓰는 목록).

DB(PostgreSQL, src/db/database.py)의 job_role 테이블(인호 스키마, alembic 초기 마이그레이션)에서 읽는다.
- job_role: job_role_id, name, normalized_name, category, description, standard_code, is_active
테이블·칸 이름은 JOB_QUERY 한 곳에만 둔다.

DB가 꺼져 있거나 테이블이 없으면 None을 돌려준다 — 1단계는 목록 없이("표준 직무 목록: 없음") 그대로 진행.
"""
import logging

log = logging.getLogger(__name__)

# 활성 직무의 이름만 (별칭 테이블은 없음)
JOB_QUERY = "SELECT name FROM job_role WHERE is_active ORDER BY name"


def load_job_catalog() -> list[str] | None:
    """DB의 표준 직무 이름 목록. 접속·조회에 실패하거나 비어 있으면 None."""
    try:
        # DB 패키지·접속 설정이 없어도 엔진의 다른 부분은 돌아가도록 호출할 때 import
        from sqlalchemy import text
        from src.db.database import engine

        with engine.connect() as conn:
            names = [r[0].strip() for r in conn.execute(text(JOB_QUERY)) if r[0] and r[0].strip()]
    except Exception as e:
        log.warning("표준 직무 목록을 DB에서 못 읽음 — 목록 없이 진행: %s", e)
        return None
    return names or None
