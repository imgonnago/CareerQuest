from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select

from src.collectors.public_data import qnet_collector as api
from src.collectors.common.api_quota import ApiQuotaExceeded
from src.db.database import SessionLocal
from src.db.models import CertificateDetail, CertificateSection, Resource, SourceDocument
from src.loaders.common import upsert_source_document, utc_now

SOURCE_PAGE = 'https://www.data.go.kr/data/15082998/fileData.do'
CATALOG_SOURCE = 'QNET_OFFICIAL_CATALOG'
DETAIL_SOURCE = 'QNET_ITEM_API'
PROGRESS = Path('data/raw/certificates/qnet_expand_progress.json')
KST = ZoneInfo('Asia/Seoul')

CODE_HEADERS = {'종목코드', '자격종목코드', '자격코드', 'jmcd', 'jmcode', 'itemcode'}
NAME_HEADERS = {'종목명', '자격종목명', '자격명', 'jm nm', 'jmnm', 'itemname'}
RELATED_TERMS = (
    '의료', '의공', '보건', '병원', '간호', '재활', '임상', '위생', '응급', '안경', '안전',
    '산업보건', '산업위생', '화학', '화공', '분석', '바이오', '생물', '식품', '품질',
    '의약', '제약', '환경', '방사선', '전기', '전자', '통신', '기계', '설비', '정보',
    '데이터', '컴퓨터', '소프트웨어', '건축', '실내', '도시', '제품디자인', '시각디자인',
    '정보처리', '조사', '직업상담', '사회복지', '청소년', '경영', '회계', '서비스',
    '산업안전', '건설안전', '인간공학', '사무', '재무', '유통', '물류',
)


def normalize_header(s: str) -> str:
    return re.sub(r'[\s_()\-]+', '', (s or '').replace('\ufeff', '')).lower()


def read_catalog(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f'CSV 없음: {path}\n공식 자료에서 CSV를 내려받아 해당 경로에 저장하세요: {SOURCE_PAGE}')
    raw = path.read_bytes()
    decoded = None
    for enc in ('utf-8-sig', 'cp949', 'euc-kr'):
        try:
            decoded = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError('CSV 인코딩을 읽을 수 없습니다.')
    lines = decoded.splitlines()
    best_start = None
    dialect = None
    for start in range(min(20, len(lines))):
        try:
            d = csv.Sniffer().sniff('\n'.join(lines[start:start+4]), delimiters=',\t;|')
        except csv.Error:
            d = csv.excel
        headers = next(csv.reader([lines[start]], dialect=d), [])
        keys = {normalize_header(h) for h in headers}
        if keys & {normalize_header(c) for c in CODE_HEADERS} and keys & {normalize_header(n) for n in NAME_HEADERS}:
            best_start, dialect = start, d
            break
    if best_start is None:
        print('CSV 첫 줄:', lines[:3])
        raise ValueError('종목코드·종목명 헤더를 찾지 못했습니다. 위 첫 줄을 확인하세요.')
    reader = csv.DictReader(lines[best_start:], dialect=dialect)
    fields = {normalize_header(x): x for x in (reader.fieldnames or [])}
    code_col = next(fields[normalize_header(x)] for x in CODE_HEADERS if normalize_header(x) in fields)
    name_col = next(fields[normalize_header(x)] for x in NAME_HEADERS if normalize_header(x) in fields)
    print(f'[CSV] 인코딩/헤더 확인: code={code_col}, name={name_col}')
    records = {}
    clashes = []
    for row in reader:
        code = str(row.get(code_col) or '').strip().upper()
        name = re.sub(r'\s+', ' ', str(row.get(name_col) or '')).strip()
        if not code or not name or not re.fullmatch(r'[0-9A-Z]{2,15}', code):
            continue
        if code in records and records[code]['name'] != name:
            clashes.append((code, records[code]['name'], name))
            continue
        records[code] = {'code':code, 'name':name, 'raw':{str(k):str(v or '') for k,v in row.items() if k is not None}}
    if clashes:
        raise ValueError(f'같은 종목코드의 이름 충돌 {len(clashes)}건. 예: {clashes[:3]}')
    if not records:
        raise ValueError('유효한 종목코드가 0건입니다. 파일 양식을 확인하세요.')
    return list(records.values())


def save_catalog(rows: list[dict]) -> tuple[int,int]:
    created = updated = 0
    with SessionLocal() as db:
        with db.begin():
            for item in rows:
                code, name = item['code'], item['name']
                cert = db.scalar(select(CertificateDetail).where(CertificateDetail.qualification_code == code))
                resource = db.get(Resource, cert.resource_id) if cert else None
                if resource is None:
                    resource = db.scalar(select(Resource).where(Resource.dedup_key == f'CERTIFICATE:{code}'))
                if resource is None:
                    resource = Resource(resource_type='CERTIFICATE', dedup_key=f'CERTIFICATE:{code}', name=name,
                                        organization='한국산업인력공단', is_active=False)
                    db.add(resource)
                    db.flush()
                    created += 1
                else:
                    if resource.resource_type != 'CERTIFICATE':
                        raise RuntimeError(f'dedup_key 충돌: {code}')
                    updated += 1
                # 기존 리소스명·상태는 변경하지 않는다.
                if cert is None:
                    cert = CertificateDetail(resource_id=resource.resource_id, qualification_code=code,
                                             administering_org='한국산업인력공단')
                    db.add(cert)
                meta = {'catalog_url':SOURCE_PAGE, 'catalog_code':code, 'catalog_name':name, 'raw':item['raw'],
                        'qualification_validity_confirmed':False, 'exam_eligibility_confirmed':False}
                upsert_source_document(db, resource_id=resource.resource_id, source_name=CATALOG_SOURCE,
                    source_type='PUBLIC_DATA', document_type='CERTIFICATE_INFO', source_record_key=code,
                    source_url=SOURCE_PAGE, title=name, content=f'공식 국가자격 종목 목록: {name} (종목코드 {code})',
                    raw_metadata=meta, discovery_channel='OFFICIAL_CSV', validation_status='ACCEPTED',
                    trust_level='A', evidence_eligible=False)
    return created, updated


def load_progress() -> set[str]:
    if not PROGRESS.is_file():
        return set()
    return set(json.loads(PROGRESS.read_text(encoding='utf-8')).get('completed_codes', []))


def save_progress(done: set[str]) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS.with_suffix('.tmp')
    tmp.write_text(json.dumps({'completed_codes':sorted(done),
                               'updated_at':datetime.now(KST).isoformat()},ensure_ascii=False, indent=2),encoding='utf-8')
    tmp.replace(PROGRESS)


def select_detail_targets(related_only: bool, refresh: bool) -> list[tuple[str,str]]:
    with SessionLocal() as db:
        rows = db.execute(select(CertificateDetail.qualification_code, Resource.name)
                          .join(Resource, Resource.resource_id == CertificateDetail.resource_id)
                          .where(Resource.resource_type == 'CERTIFICATE')).all()
        have = set(db.scalars(select(SourceDocument.source_record_key)
                              .where(SourceDocument.source_name == DETAIL_SOURCE)).all())
    done = set() if refresh else load_progress()
    result = []
    for code, name in rows:
        if not re.fullmatch('[0-9A-Z]{2,15}', code or ''):
            continue  # 국시원 등 별도 발급 코드 제외
        if related_only and not any(t in name for t in RELATED_TERMS):
            continue
        if not refresh and (code in done or any(s.startswith(f'DETAIL:{code}:') for s in have)):
            continue
        result.append((code, name))
    return sorted(result, key=lambda x: (not any(t in x[1] for t in RELATED_TERMS), x[1]))


def save_details(code: str, name: str, entries: list[dict]) -> int:
    sections = {}
    for item in entries:
        typ = str(item.get('info_type') or '').strip()[:100]
        content = str(item.get('content') or '').strip()
        if '<' in content and '>' in content:
            content = BeautifulSoup(content, 'html.parser').get_text(' ', strip=True)
        content = re.sub(r'\s+', ' ', content).strip()
        if typ and len(content) >= 30:
            sections[typ] = (content[:16000], item)
    if not sections:
        return 0
    with SessionLocal() as db:
        with db.begin():
            cert = db.scalar(select(CertificateDetail).where(CertificateDetail.qualification_code == code))
            if cert is None:
                raise RuntimeError(f'DB 종목코드 없음: {code}')
            res = db.get(Resource, cert.resource_id)
            if res is None or res.resource_type != 'CERTIFICATE':
                raise RuntimeError(f'리소스 연결 문제: {code}')
            for idx, (typ, (content, item)) in enumerate(sections.items(), 1):
                sd = upsert_source_document(db, resource_id=res.resource_id, source_name=DETAIL_SOURCE,
                    source_type='OFFICIAL_API', document_type='CERTIFICATE_INFO',
                    source_record_key=f'DETAIL:{code}:{typ}', source_url=api.ITEM_URL,
                    title=f'{name} - {typ}', content=content, raw_metadata=item, discovery_channel='QNET_DETAIL_API',
                    fetch_status='SUCCESS', validation_status='ACCEPTED', trust_level='A',
                    evidence_eligible=len(content)>=80, http_status=200)
                sec = db.scalar(select(CertificateSection).where(CertificateSection.resource_id==res.resource_id,
                                                                 CertificateSection.section_type==typ))
                if sec is None:
                    sec = CertificateSection(resource_id=res.resource_id, section_type=typ)
                    db.add(sec)
                sec.section_order = idx
                sec.content = content
                sec.source_document_id = sd.source_document_id
            if not res.description or len(res.description) < 80:
                res.description = next(iter(sections.values()))[0][:1500]
            # 자격 유효 상태와 응시 자격은 별도 확인하기 전까지 변경하지 않는다.
    return len(sections)


def main() -> None:
    p = argparse.ArgumentParser(description='CareerQuest Q-Net 공식 종목 목록/상세 확장')
    p.add_argument('--mode', choices=['catalog','details'], required=True)
    p.add_argument('--file', default='data/raw/certificates/qnet_catalog.csv')
    p.add_argument('--limit', type=int, default=None, help='catalog 미리보기/적재 최대 건수')
    p.add_argument('--max-details', type=int, default=50, help='한 번 실행할 최대 상세 API 종목')
    p.add_argument('--related-only', action='store_true', help='전공 관련 키워드가 포함된 종목만')
    p.add_argument('--refresh', action='store_true', help='상세 기존 수집분도 재요청')
    p.add_argument('--delay', type=float, default=1.5)
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    if args.delay < 1 or (args.limit is not None and args.limit < 1) or args.max_details < 1:
        p.error('delay>=1, limit>=1, max-details>=1 이어야 합니다.')
    if args.mode == 'catalog':
        rows = read_catalog(Path(args.file))
        if args.related_only:
            rows = [r for r in rows if any(t in r['name'] for t in RELATED_TERMS)]
        if args.limit:
            rows = rows[:args.limit]
        print(f'[CATALOG] 대상={len(rows)}, APPLY={args.apply}')
        for row in rows[:15]:
            print(' ', row['code'], row['name'])
        if args.apply:
            created, updated = save_catalog(rows)
            print(f'[COMMIT] 신규={created}, 기존={updated}')
        else:
            print('[DRY RUN] DB 변경 없음')
        return
    if not api.SERVICE_KEY or not api.ITEM_URL:
        raise RuntimeError('.env의 DATA_GO_KR_API_KEY와 DATA_GO_QNET_ITEM_URL을 확인하세요.')
    targets = select_detail_targets(args.related_only, args.refresh)[:args.max_details]
    print(f'[DETAILS] 대상={len(targets)} APPLY={args.apply} (API 일일 quota 적용)')
    done = load_progress()
    ok = weak = errors = 0
    for i, (code, name) in enumerate(targets, 1):
        try:
            items = api.collect_qualification_detail(code)
            valid = [x for x in items if x.get('info_type') and x.get('content')]
            print(f'[{i}/{len(targets)}] {code} {name} | 원본 섹션={len(items)}, 내용={len(valid)}')
            if not valid:
                weak += 1
                continue
            if args.apply:
                count = save_details(code, name, valid)
                if not count:
                    weak += 1
                    continue
                done.add(code)
                save_progress(done)
                print(f'  DB 저장: 섹션 {count}개')
            else:
                print('  DRY RUN:',str(valid[0].get('content',''))[:160])
            ok += 1
        except ApiQuotaExceeded as exc:
            print('[API 쿼터 도달, 중단]', exc)
            break
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f'[ERROR] {code}: {exc}')
            errors += 1
            if errors >= 5:
                print('오류 5건 누적, 중단')
                break
        time.sleep(args.delay)
    print(f'[결과] 성공={ok}, 본문부족={weak}, 오류={errors}')


if __name__ == '__main__':
    main()
