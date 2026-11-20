"""Read-only quarantine for StudentSkill rows supported *only* by unapproved ResourceSkill.

Policy:
- Completed course/certificate/program evidence counts for GAP only when its
  matching ResourceSkill row is verified=True and relation type is valid.
- A separate non-resource evidence for the same StudentSkill preserves it.
- StudentSkill with *no* evidence is preserved (may be a direct/manual/profile entry);
  its provenance cannot be established from this table alone.
- This module never deletes or changes StudentSkill or evidence.
"""

from sqlalchemy import text
from src.db.database import engine

UNTRUSTED_ONLY_SQL = text("""
SELECT ss.skill_id
FROM student_skill ss
WHERE ss.student_id = :student_id
  AND EXISTS (
      SELECT 1
      FROM student_skill_evidence e
      WHERE e.student_skill_id = ss.student_skill_id
        AND e.evidence_type IN ('COURSE', 'CERTIFICATE', 'PROGRAM')
  )
  AND NOT EXISTS (
      SELECT 1
      FROM student_skill_evidence e
      WHERE e.student_skill_id = ss.student_skill_id
        AND (e.evidence_type NOT IN ('COURSE', 'CERTIFICATE', 'PROGRAM')
             OR e.evidence_type IS NULL)
  )
  AND NOT EXISTS (
      SELECT 1
      FROM student_skill_evidence e
      JOIN student_resource sr
        ON sr.student_id = ss.student_id
       AND sr.resource_id = e.resource_id
       AND sr.status = 'COMPLETED'
      JOIN resource r
        ON r.resource_id = e.resource_id
       AND r.resource_type = e.evidence_type
      JOIN resource_skill rs
        ON rs.resource_id = r.resource_id
       AND rs.skill_id = ss.skill_id
       AND rs.verified = TRUE
      WHERE e.student_skill_id = ss.student_skill_id
        AND (
          (r.resource_type = 'COURSE' AND rs.relation_type = 'TAUGHT')
          OR (r.resource_type = 'CERTIFICATE' AND rs.relation_type = 'VERIFIED')
          OR (r.resource_type = 'PROGRAM' AND rs.relation_type = 'TAUGHT')
        )
  )
""")


def find_untrusted_only_skill_ids(student_id: int) -> set[int]:
    """Return StudentSkill IDs to quarantine for GAP; never writes the DB."""
    if student_id <= 0:
        raise ValueError("student_id must be positive")
    with engine.connect() as conn:
        result = conn.execute(
            UNTRUSTED_ONLY_SQL, {"student_id": student_id}
        ).scalars().all()
    return {int(skill_id) for skill_id in result}
