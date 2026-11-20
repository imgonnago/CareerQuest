"""Read-only live PostgreSQL Skill schema contract audit."""
from __future__ import annotations

from sqlalchemy import text
from src.db.database import engine

TABLES: dict[str, set[str]] = {
    "skill": {"skill_id", "normalized_name", "is_active"},
    "skill_alias": {"skill_alias_id", "skill_id", "normalized_alias"},
    "resource": {"resource_id", "resource_type", "is_active"},
    "source_document": {"source_document_id", "resource_id", "content", "validation_status", "evidence_eligible"},
    "resource_skill": {"resource_skill_id", "resource_id", "skill_id", "relation_type", "verified", "source_document_id", "confidence"},
    "student_resource": {"student_id", "resource_id", "status"},
    "student_skill": {"student_skill_id", "student_id", "skill_id", "verified"},
    "student_skill_evidence": {"student_skill_id", "evidence_type", "resource_id", "experience_id"},
    "job_role": {"job_role_id", "is_active"},
    "role_skill": {"role_skill_id", "job_role_id", "skill_id"},
}
UNIQUE_CONTRACTS = {
    "skill": ("normalized_name",),
    "skill_alias": ("normalized_alias",),
    "resource_skill": ("resource_id", "skill_id", "relation_type"),
    "student_skill": ("student_id", "skill_id"),
    "role_skill": ("job_role_id", "skill_id"),
}


def main():
    errors = []
    with engine.connect() as conn:
        db = conn.execute(text("SELECT current_database()" )).scalar_one()
        schema = conn.execute(text("SELECT current_schema()" )).scalar_one()
        print(f"[DB CONTRACT] database={db}, schema={schema}")
        for tbl, expected in TABLES.items():
            rows = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=:schema AND table_name=:table
            """), {"schema":schema, "table":tbl}).scalars().all()
            found = set(rows)
            missing = sorted(expected-found)
            print(f"[{'FAIL' if missing else 'PASS'}] {tbl}: missing={missing}")
            if missing: errors.append(f"{tbl} missing {missing}")
        # UNIQUE constraints can also be enforced by a standalone UNIQUE INDEX.
        # pg_constraint alone misses those valid database protections.
        for tbl, expected in UNIQUE_CONTRACTS.items():
            rows = conn.execute(text("""
                SELECT ix.indexrelid::regclass::text AS index_name,
                       ARRAY(
                           SELECT att.attname
                           FROM unnest(ix.indkey::smallint[]) WITH ORDINALITY
                                AS pos(attnum, ord)
                           JOIN pg_attribute att
                             ON att.attrelid = tab.oid
                            AND att.attnum = pos.attnum
                           WHERE pos.ord <= ix.indnkeyatts
                           ORDER BY pos.ord
                       ) AS key_columns
                FROM pg_index ix
                JOIN pg_class tab ON tab.oid = ix.indrelid
                JOIN pg_namespace ns ON ns.oid = tab.relnamespace
                WHERE ns.nspname = :schema
                  AND tab.relname = :table
                  AND ix.indisunique = TRUE
                  AND ix.indisvalid = TRUE
                  AND ix.indisready = TRUE
                  AND ix.indpred IS NULL
                  AND ix.indexprs IS NULL
            """), {"schema": schema, "table": tbl}).mappings().all()
            matches = [r["index_name"] for r in rows
                       if tuple(r["key_columns"]) == expected]
            if matches:
                print(f"[PASS] unique({tbl}, {','.join(expected)}) by {matches[0]}")
            else:
                # Static whitelist from UNIQUE_CONTRACTS: no user SQL identifiers.
                col = expected[0] if len(expected) == 1 else None
                if col is not None:
                    dup = conn.execute(text(f"""
                        SELECT COUNT(*) FROM (
                            SELECT {col}
                            FROM {schema}.{tbl}
                            GROUP BY {col}
                            HAVING COUNT(*) > 1
                        ) dup
                    """)).scalar_one()
                    print(f"[FAIL] unique({tbl}, {col}) missing DB enforcement; duplicate_groups={dup}")
                else:
                    print(f"[FAIL] unique({tbl}, {','.join(expected)}) missing DB enforcement")
                errors.append(f"{tbl} lacks a valid unfiltered unique index on {expected}")
        for tbl in ("resource_skill_proposal", "resource_skill_proposal_event"):
            exists = bool(conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"),
                {"name":schema + '.' + tbl}).scalar_one())
            print(f"[{'PASS' if exists else 'PENDING'}] optional review queue: {tbl}")
    print("[READ ONLY] No DB changes")
    if errors:
        raise SystemExit("[FAIL] Schema contract errors: " + "; ".join(errors))
    print("[PASS] Core Skill database structure")


if __name__ == "__main__":
    main()
