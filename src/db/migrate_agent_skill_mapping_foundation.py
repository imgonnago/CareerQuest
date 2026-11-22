"""Safe additive migration for CareerQuest agent mapping observability schema.

Default is READ ONLY. --apply requires an explicit confirmation on LOCAL careerquest DB.
Creates four isolated tables and one disabled-auto-approval policy row; does not
run an LLM, import model-team tools, or change existing Skill mapping data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from src.db.database import engine

SQL_FILE = (Path(__file__).resolve().parent / 'migrations'
            / '20261122_agent_skill_mapping_foundation.sql')
TABLES = (
    'agent_skill_mapping_policy',
    'agent_skill_mapping_run',
    'agent_skill_mapping_attempt',
    'agent_skill_mapping_tool_event',
)
REQUIRED_EXISTING = (
    'resource', 'source_document', 'skill', 'resource_skill_proposal',
)
EXPECTED_COLS = {
    TABLES[0]: {'policy_id', 'policy_version', 'allow_auto_approve',
                'allow_auto_apply', 'default_route', 'max_retries'},
    TABLES[1]: {'run_id', 'correlation_key', 'agent_name', 'source_kind',
                'resource_id', 'source_document_id', 'source_sha256',
                'policy_id', 'mode', 'status', 'context_metadata'},
    TABLES[2]: {'attempt_id', 'run_id', 'attempt_no', 'retry_of_attempt_id',
                'candidate_skill_id', 'candidate_skill_name', 'validation_status',
                'risk_level', 'route_decision', 'linked_proposal_id'},
    TABLES[3]: {'event_id', 'run_id', 'attempt_id', 'event_seq', 'tool_name',
                'decision_origin', 'tool_status', 'request_sha256', 'event_metadata'},
}
EXPECTED_INDEXES = {
    'ix_agent_mapping_run_status_created',
    'ix_agent_mapping_run_resource_created',
    'ix_agent_mapping_attempt_route',
    'ix_agent_tool_event_run_time',
    'ix_agent_tool_event_attempt',
}


def sql_statements(contents: str) -> list[str]:
    return [part.strip() for part in contents.split('-- CQ_STATEMENT')[1:]
            if part.strip()]


def _exists(conn, name: str) -> bool:
    return bool(conn.execute(text('SELECT to_regclass(:name) IS NOT NULL'),
                             {'name': 'public.' + name}).scalar_one())


def inspect(conn) -> dict[str, bool]:
    found = {table: _exists(conn, table) for table in TABLES}
    if any(found.values()) and not all(found.values()):
        raise RuntimeError(f'[STOP] Partial agent schema detected: {found}; inspect manually')
    if all(found.values()):
        for name in TABLES:
            cols = set(conn.execute(text('''
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:table
            '''), {'table': name}).scalars())
            missing = EXPECTED_COLS[name] - cols
            if missing:
                raise RuntimeError(f'[STOP] {name} missing columns: {sorted(missing)}')
        indexes = set(conn.execute(text('''
            SELECT indexname FROM pg_indexes
            WHERE schemaname='public'
              AND tablename IN ('agent_skill_mapping_run',
                                'agent_skill_mapping_attempt',
                                'agent_skill_mapping_tool_event')
        ''')).scalars())
        missing = EXPECTED_INDEXES - indexes
        if missing:
            raise RuntimeError(f'[STOP] Agent indexes missing: {sorted(missing)}')
        policy = conn.execute(text('''
            SELECT policy_version, allow_auto_approve, allow_auto_apply,
                   default_route, max_retries
            FROM agent_skill_mapping_policy WHERE policy_id=1
        ''')).mappings().first()
        if policy is None:
            raise RuntimeError('[STOP] Default manual-review policy row is missing')
        if policy['allow_auto_approve'] or policy['allow_auto_apply']:
            raise RuntimeError('[STOP] Auto approval/apply policy unexpectedly enabled')
        if policy['default_route'] != 'HUMAN_REVIEW':
            raise RuntimeError('[STOP] Default agent routing is not HUMAN_REVIEW')
        print('[POLICY] human review mandatory, auto-approve=OFF, auto-apply=OFF')
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='explicitly create the four tables (with confirmation)')
    args = parser.parse_args()

    if not SQL_FILE.is_file():
        raise SystemExit(f'[STOP] Migration SQL missing: {SQL_FILE}')
    statements = sql_statements(SQL_FILE.read_text(encoding='utf-8'))
    if len(statements) != 10:
        raise SystemExit(f'[STOP] Expected 10 migration statements, got {len(statements)}')

    with engine.connect() as conn:
        database_name = conn.execute(text('SELECT current_database()')).scalar_one()
        schema = conn.execute(text('SELECT current_schema()')).scalar_one()
        found = inspect(conn)
        prereq = {name: _exists(conn, name) for name in REQUIRED_EXISTING}
    print(f'[DB] {database_name}, schema={schema}, agent_tables={found}')
    print(f'[PREREQUISITES] {prereq}')
    if all(found.values()):
        print('[ALREADY EXISTS] Schema verified. No database writes.')
        return
    if not all(prereq.values()):
        raise SystemExit('[STOP] Prior ResourceSkill proposal schema must exist first')
    if schema != 'public':
        raise SystemExit('[STOP] Expected schema public')
    if not args.apply:
        print('[PREVIEW ONLY] Will create four new agent tables and one policy row.')
        print('[SAFETY] Existing ResourceSkill, StudentSkill, RoleSkill untouched.')
        print('[AUTO APPROVAL] OFF. No LLM connection or tests.')
        print('[NEXT] Add --apply to explicitly create the schema.')
        return

    allowed_hosts = {'localhost', '127.0.0.1', '::1'}
    if str(engine.url.host).lower() not in allowed_hosts:
        raise SystemExit('[STOP] Apply is restricted to local PostgreSQL only')
    if database_name != 'careerquest':
        raise SystemExit('[STOP] Apply is restricted to local DB named careerquest')
    token = f'CREATE_AGENT_MAPPING_SCHEMA_ON_{database_name}'
    if input(f'Type {token} to CREATE four NEW tables: ').strip() != token:
        raise SystemExit('[CANCEL] Database unchanged')

    # Single atomic transaction: a failed CREATE leaves no partially added tables.
    with engine.begin() as conn:
        current = inspect(conn)
        if any(current.values()):
            raise RuntimeError('[STOP] Agent schema appeared during migration')
        conn.execute(text('SELECT pg_advisory_xact_lock(741516982641::bigint)'))
        for statement in statements:
            conn.execute(text(statement))

    with engine.connect() as conn:
        created = inspect(conn)
        policy = conn.execute(text('''
            SELECT policy_version, allow_auto_approve, allow_auto_apply
            FROM agent_skill_mapping_policy WHERE policy_id=1
        ''')).mappings().one()
        rows = {name: conn.execute(text('SELECT count(*) FROM ' + name)).scalar_one()
                for name in TABLES}
    print(f'[CREATED] {created}')
    print(f'[ROWS] {rows}')
    print(f'[POLICY] {dict(policy)}')
    print('[DONE] Agent DB foundation only. LLM integration intentionally deferred.')


if __name__ == '__main__':
    main()
