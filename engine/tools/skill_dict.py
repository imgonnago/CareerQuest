"""DB Skill 사전 조회 (3단계 근거 집계가 Skill 이름을 skill_id로 바꿀 때 쓰는 사전).

인호 브랜치(src/db/models.py)의 skill·skill_alias 테이블을 읽는다.
- skill: skill_id, name, normalized_name, category, is_active
- skill_alias: skill_id, alias, normalized_alias

DB가 꺼져 있거나 테이블이 없으면 None을 돌려준다 — 3단계는 skill_id 없이(null) 이름만으로 집계한다.
"""
import logging
from collections import defaultdict

from engine.schemas.aggregate import SkillEntry

log = logging.getLogger(__name__)

SKILL_QUERY = """
SELECT s.skill_id, s.name, s.category, sa.alias
FROM skill s
LEFT JOIN skill_alias sa ON sa.skill_id = s.skill_id
WHERE s.is_active
ORDER BY s.skill_id
"""


def load_skill_dict() -> list[SkillEntry] | None:
    """DB의 Skill 사전(활성 Skill + 별칭). 접속·조회에 실패하거나 비어 있으면 None."""
    try:
        # DB 패키지·접속 설정이 없어도 엔진의 다른 부분은 돌아가도록 호출할 때 import
        from sqlalchemy import text
        from src.db.database import engine

        with engine.connect() as conn:
            rows = conn.execute(text(SKILL_QUERY)).all()
    except Exception as e:
        log.warning("Skill 사전을 DB에서 못 읽음 — skill_id 없이 집계: %s", e)
        return None
    entries: dict[int, SkillEntry] = {}
    aliases = defaultdict(list)
    for skill_id, name, category, alias in rows:
        entries.setdefault(skill_id, SkillEntry(skill_id=skill_id, name=name, category=category))
        if alias:
            aliases[skill_id].append(alias)
    for skill_id, a in aliases.items():
        entries[skill_id].aliases = a
    return list(entries.values()) or None
