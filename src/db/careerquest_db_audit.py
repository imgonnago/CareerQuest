from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import text
from src.db.database import engine

KST = ZoneInfo('Asia/Seoul')

CHECKS = {
    'resource_counts': '''
        SELECT resource_type, COUNT(*) AS total,
          COUNT(*) FILTER (WHERE is_active) AS active,
          COUNT(*) FILTER (WHERE canonical_url IS NULL OR trim(canonical_url)='') AS missing_url
        FROM resource GROUP BY resource_type ORDER BY resource_type
    ''',
    'evidence_by_resource_type': '''
        SELECT r.resource_type, COUNT(*) AS documents,
          COUNT(*) FILTER (WHERE sd.evidence_eligible) AS evidence_eligible,
          COUNT(*) FILTER (WHERE length(trim(COALESCE(sd.content,''))) < 80) AS short_text,
          COUNT(*) FILTER (WHERE sd.evidence_eligible AND length(trim(COALESCE(sd.content,''))) < 80) AS questionable_evidence
        FROM source_document sd JOIN resource r ON r.resource_id=sd.resource_id
        GROUP BY r.resource_type ORDER BY r.resource_type
    ''',
    'source_health': '''
        SELECT sd.source_name, COUNT(*) AS total,
          COUNT(*) FILTER (WHERE sd.evidence_eligible) AS usable,
          COUNT(*) FILTER (WHERE sd.validation_status='PENDING') AS pending,
          COUNT(*) FILTER (WHERE sd.content IS NULL OR length(trim(sd.content))<80) AS short_text
        FROM source_document sd
        GROUP BY sd.source_name ORDER BY total DESC, sd.source_name
    ''',
    'missing_detail': '''
        SELECT 'COURSE' AS resource_type, COUNT(*) AS missing FROM resource r
        WHERE r.resource_type='COURSE' AND NOT EXISTS(SELECT 1 FROM course_detail d WHERE d.resource_id=r.resource_id)
        UNION ALL
        SELECT 'JOB', COUNT(*) FROM resource r
        WHERE r.resource_type='JOB' AND NOT EXISTS(SELECT 1 FROM job_detail d WHERE d.resource_id=r.resource_id)
        UNION ALL
        SELECT 'PROGRAM', COUNT(*) FROM resource r
        WHERE r.resource_type='PROGRAM' AND NOT EXISTS(SELECT 1 FROM program_detail d WHERE d.resource_id=r.resource_id)
        UNION ALL
        SELECT 'CERTIFICATE', COUNT(*) FROM resource r
        WHERE r.resource_type='CERTIFICATE' AND NOT EXISTS(SELECT 1 FROM certificate_detail d WHERE d.resource_id=r.resource_id)
    ''',
    'source_orphans': '''
        SELECT COUNT(*) AS orphan_documents
        FROM source_document sd
        LEFT JOIN resource r ON sd.resource_id = r.resource_id
        WHERE r.resource_id IS NULL
    ''',
    'resources_without_source': '''
        SELECT resource_type, COUNT(*) AS without_source
        FROM resource r
        WHERE NOT EXISTS(SELECT 1 FROM source_document sd WHERE sd.resource_id = r.resource_id)
        GROUP BY resource_type ORDER BY resource_type
    ''',
    'title_only_program_sources': '''
        SELECT sd.source_name, COUNT(*) AS title_only,
          COUNT(*) FILTER (WHERE sd.evidence_eligible) AS incorrectly_eligible
        FROM source_document sd
        JOIN resource r ON r.resource_id=sd.resource_id
        WHERE r.resource_type='PROGRAM' AND (
          trim(COALESCE(sd.content,''))='' OR
          trim(COALESCE(sd.content,''))=trim(COALESCE(sd.title,''))
        )
        GROUP BY sd.source_name ORDER BY title_only DESC
    ''',
    'program_past_active': '''
        SELECT COUNT(*) AS active_past_end
        FROM resource r JOIN program_detail pd ON pd.resource_id=r.resource_id
        WHERE r.is_active AND pd.application_end IS NOT NULL AND pd.application_end < NOW()
    ''',
    'jobs_by_parse_status': '''
        SELECT parse_status, COUNT(*) AS total
        FROM job_detail GROUP BY parse_status ORDER BY total DESC
    ''',
    'job_past_deadline_active': '''
        SELECT COUNT(*) AS active_past_deadline
        FROM resource r JOIN job_detail jd ON jd.resource_id=r.resource_id
        WHERE r.is_active AND jd.deadline IS NOT NULL AND jd.deadline < NOW()
    ''',
    'certificates_summary': '''
        SELECT COUNT(*) AS certificates,
         COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM certificate_section cs WHERE cs.resource_id=cd.resource_id)) AS with_sections,
         COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM source_document sd WHERE sd.resource_id=cd.resource_id AND sd.evidence_eligible)) AS with_usable_evidence
        FROM certificate_detail cd
    ''',
    'certificates_duplicate_names': '''
        SELECT lower(trim(r.name)) AS canonical_name, COUNT(*) AS copies,
         string_agg(coalesce(cd.qualification_code,'?'), ', ' ORDER BY cd.qualification_code) AS codes
        FROM resource r JOIN certificate_detail cd ON cd.resource_id=r.resource_id
        GROUP BY lower(trim(r.name)) HAVING COUNT(*)>1
        ORDER BY copies DESC, canonical_name LIMIT 30
    ''',
    'program_duplicate_titles': '''
        SELECT lower(trim(name)) AS title, COUNT(*) AS copies
        FROM resource WHERE resource_type='PROGRAM'
        GROUP BY lower(trim(name)) HAVING COUNT(*)>1
        ORDER BY copies DESC, title LIMIT 30
    ''',
    'courses_per_department': '''
        SELECT d.name AS department, COUNT(cd.resource_id) AS courses
        FROM department d LEFT JOIN course_detail cd ON cd.department_id=d.department_id
        GROUP BY d.department_id, d.name ORDER BY courses, department
    ''',
    'resource_link_integrity': '''
        SELECT COUNT(*) AS resources_total,
          COUNT(*) FILTER (WHERE name IS NULL OR trim(name)='') AS blank_names,
          COUNT(*) FILTER (WHERE dedup_key IS NULL OR trim(dedup_key)='') AS missing_dedup_keys
        FROM resource
    ''',
}


def main():
    p = argparse.ArgumentParser(description='CareerQuest read-only DB audit')
    p.add_argument('--output', default=None)
    args = p.parse_args()
    results = {'checked_at': datetime.now(KST).isoformat(), 'checks': {}, 'errors': {}}
    with engine.connect() as conn:
        # BEGIN+ROLLBACK 강제: 진단 쿼리만 허용, 데이터 변경 방지
        conn.execute(text('SET TRANSACTION READ ONLY'))
        for name, sql in CHECKS.items():
            try:
                # 하나의 쿼리가 실패해도 나머지 쿼리를 실행할 수 있도록 savepoint 사용
                conn.execute(text('SAVEPOINT cq_audit'))
                rows = [dict(x) for x in conn.execute(text(sql)).mappings().all()]
                results['checks'][name] = rows
                conn.execute(text('RELEASE SAVEPOINT cq_audit'))
                print(f'[OK] {name}: {len(rows)}행')
            except Exception as exc:
                try:
                    conn.execute(text('ROLLBACK TO SAVEPOINT cq_audit'))
                    conn.execute(text('RELEASE SAVEPOINT cq_audit'))
                except Exception:
                    pass
                results['errors'][name] = str(exc)
                print(f'[ERROR] {name}: {exc}')
        conn.rollback()
    path = Path(args.output) if args.output else Path('data/reports') / ('careerquest_db_audit_' + datetime.now(KST).strftime('%Y%m%d_%H%M%S') + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print('\n[전체 자원]', results['checks'].get('resource_counts'))
    print('[상세테이블 누락]', results['checks'].get('missing_detail'))
    print('[제목만 있는 PROGRAM]', results['checks'].get('title_only_program_sources'))
    print('[CERTIFICATE]', results['checks'].get('certificates_summary'))
    print('[감사 보고서]', path)
    print('[쿼리 오류]', len(results['errors']))
    print('참고: 중복 제목은 동일 행사의 재게시 가능성이 있으므로 삭제 전 수동 검토가 필요합니다.')


if __name__=='__main__':
    main()
