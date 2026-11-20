"""Read-only audit comparing legacy confidence-based vs approved-only JOB Skill aggregation.

No SQL mutations. Does not call aggregator's --save.
"""
from __future__ import annotations

from sqlalchemy import text
from src.db.database import engine

BASE_CTE = """
WITH eligible AS (
    SELECT
        rs.resource_skill_id,
        rs.resource_id,
        rs.skill_id,
        rs.relation_type,
        rs.mapping_method,
        rs.verified,
        COALESCE(rs.confidence, 0) AS confidence,
        rjr.job_role_id,
        jr.name AS role_name
    FROM resource_skill rs
    JOIN resource r ON r.resource_id = rs.resource_id
    JOIN skill s ON s.skill_id = rs.skill_id
    JOIN resource_job_role rjr ON rjr.resource_id = r.resource_id
        AND rjr.is_primary = TRUE
        AND COALESCE(rjr.confidence, 0) >= 0.80
    JOIN job_role jr ON jr.job_role_id = rjr.job_role_id
    WHERE r.resource_type = 'JOB'
      AND r.is_active = TRUE
      AND jr.is_active = TRUE
      AND s.is_active = TRUE
      AND rs.relation_type IN ('REQUIRED', 'PREFERRED', 'RELATED')
)
"""


def main():
    with engine.connect() as conn:
        db = conn.execute(text("SELECT current_database() ")).scalar_one()
        print("[DB]", db)
        counts = conn.execute(text(BASE_CTE + """
            SELECT
                COUNT(*) AS linked_rows,
                COUNT(*) FILTER (WHERE verified) AS approved_rows,
                COUNT(*) FILTER (
                    WHERE NOT verified AND confidence >= 0.80
                ) AS confidence_only_rows,
                COUNT(DISTINCT resource_skill_id) AS unique_mappings,
                COUNT(DISTINCT job_role_id) AS roles
            FROM eligible
        """)).mappings().one()
        print("[CURRENT PIPELINE ELIGIBILITY]")
        for k, v in counts.items():
            print(" ", k, "=", v)

        print("[PER ROLE: job samples before / after approved-only filter]")
        rows = conn.execute(text(BASE_CTE + """
            SELECT
                role_name,
                job_role_id,
                COUNT(DISTINCT resource_id) FILTER (
                    WHERE verified OR confidence >= 0.80
                ) AS legacy_job_samples,
                COUNT(DISTINCT resource_id) FILTER (
                    WHERE verified
                ) AS approved_job_samples,
                COUNT(*) FILTER (
                    WHERE NOT verified AND confidence >= 0.80
                ) AS excluded_mapping_rows
            FROM eligible
            GROUP BY role_name, job_role_id
            ORDER BY role_name, job_role_id
        """)).mappings().all()
        for r in rows:
            print(
                f" {r['role_name']} (role={r['job_role_id']}): "
                f"legacy={r['legacy_job_samples']}, "
                f"approved={r['approved_job_samples']}, "
                f"excluded={r['excluded_mapping_rows']}"
            )

        stored = conn.execute(text("""
            SELECT COUNT(*) AS n, COUNT(DISTINCT job_role_id) AS roles
            FROM role_skill
        """)).mappings().one()
        print(
            "[STORED ROLE SKILL] rows=", stored['n'],
            "roles=", stored['roles'],
            "; provenance not recorded in table"
        )

        print("[TOP CONFIDENCE-ONLY MAPPING SAMPLES]")
        samples = conn.execute(text(BASE_CTE + """
            SELECT DISTINCT
                resource_skill_id,
                resource_id,
                skill_id,
                relation_type,
                mapping_method,
                confidence
            FROM eligible
            WHERE verified = FALSE AND confidence >= 0.80
            ORDER BY confidence DESC, resource_skill_id
            LIMIT 12
        """)).mappings().all()
        for r in samples:
            print(dict(r))

    print("[READ ONLY] No database writes. Do NOT use --save yet.")


if __name__ == "__main__":
    main()
