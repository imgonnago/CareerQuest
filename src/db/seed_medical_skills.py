
from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import Skill, SkillAlias
from src.db.seed_master import normalize_name


# CareerQuest 메디컬캠퍼스 Skill 확장 v1
#
# 공식 NCS 코드의 직접 복사본이 아닌
# 18학과용 초기 canonical Skill 후보 목록.
#
# 교과목 이수 = Skill 보유로 자동 판정하지 않음.
# 의료행위는 별도의 법적 면허·자격 요건 적용.
#
# 각 행: 이름 | 역량 설명

SKILL_GROUPS: dict[str, str] = {

    "HEALTHCARE_COMMON": """
환자안전 관리|의료 과정에서 사고 예방과 안전 수칙을 적용하는 역량
감염예방 및 관리|감염 전파 경로를 이해하고 예방 절차를 적용하는 역량
의료윤리|보건의료 상황에서 전문직 윤리를 적용하는 역량
의료정보 개인정보보호|환자와 임상정보 보호 원칙을 적용하는 역량
의료 의사소통|환자·보호자·의료진과 정확하게 소통하는 역량
다학제 의료협업|서로 다른 보건의료 직종과 협력하는 역량
근거기반 보건의료|연구 근거의 질을 검토하고 실무에 적용하는 역량
의료질 개선|의료서비스 질 지표와 개선 활동을 이해하는 역량
의료기관 업무 이해|의료기관의 부서와 서비스 전달 흐름을 이해하는 역량
보건의료 법규 이해|보건의료 관련 법규와 업무상 의무를 이해하는 역량
환자 교육 및 상담|대상자에게 건강정보를 이해하기 쉽게 설명하는 역량
의료 문서 작성|의료 관련 업무 문서를 정확하게 작성하는 역량
""",

    "CLINICAL_MEDICINE": """
병력청취|환자 증상과 과거력을 체계적으로 확인하는 역량
신체진찰|신체진찰을 수행하고 소견을 정리하는 역량
임상적 추론|임상 정보를 통합하여 문제를 논리적으로 검토하는 역량
감별진단 사고|질환 가능성을 비교하고 근거를 검토하는 역량
임상검사 결과 해석|검사결과의 임상적 의미와 한계를 이해하는 역량
약물 안전성 평가|약물 사용 위험과 이상반응을 검토하는 역량
임상연구 설계|임상 연구의 가설·설계·변수를 계획하는 역량
임상의학 문헌평가|의학 논문의 방법과 결과를 비판적으로 평가하는 역량
""",

    "NURSING": """
간호사정|대상자의 상태와 간호 요구를 평가하는 역량
간호계획 수립|간호 목표와 중재 계획을 수립하는 역량
간호중재 수행|계획된 간호 활동을 안전하게 수행하는 역량
활력징후 측정|체온·맥박·호흡·혈압 등을 측정하는 역량
간호 투약 안전관리|투약 확인 절차와 안전 원칙을 적용하는 역량
정맥주입 간호|정맥주입 관리와 관련 위험을 이해하는 역량
상처 및 드레싱 간호|상처 상태와 드레싱 관리 원칙을 적용하는 역량
수술 전후 간호|수술 환자 간호와 안전 절차를 이해하는 역량
중환자 간호 모니터링|중환자 상태 변화를 관찰하고 보고하는 역량
간호기록 관리|간호 관찰·중재·평가 내용을 정확히 기록하는 역량
""",

    "REHABILITATION": """
근골격계 기능평가|근골격계 움직임과 기능 제한을 평가하는 역량
관절가동범위 측정|관절 운동 범위를 표준화된 방법으로 측정하는 역량
근력 평가|근육 기능과 근력을 평가하는 역량
자세 및 정렬 평가|자세와 신체 정렬 상태를 평가하는 역량
보행 분석|보행 패턴과 이동 기능을 평가하는 역량
운동치료 계획|기능 개선을 위한 운동치료 계획을 수립하는 역량
신경계 재활 평가|신경계 손상에 따른 기능 제한을 평가하는 역량
통증 평가|통증의 정도·양상·기능 영향을 평가하는 역량
보조기기 적용 평가|보조기기의 적합성을 평가하는 역량
일상생활활동 평가|일상생활 수행 수준을 평가하는 역량
작업수행 분석|일상·학습·직업 활동의 수행 요소를 분석하는 역량
인지재활 평가|인지 기능과 일상 수행의 영향을 평가하는 역량
감각통합 평가|감각처리와 활동 수행에 미치는 영향을 평가하는 역량
재활 목표 설정|재활 대상자의 기능 중심 목표를 설정하는 역량
""",

    "CLINICAL_LAB": """
임상 검체 관리|임상 검체의 식별·보관·운송 원칙을 적용하는 역량
임상화학 검사|생화학적 검사 원리와 분석 절차를 이해하는 역량
혈액학 검사|혈액 성분 검사와 분석 절차를 이해하는 역량
임상미생물 검사|미생물 검사 원리와 절차를 이해하는 역량
면역혈청 검사|면역학적 진단검사 절차를 이해하는 역량
분자진단 검사|핵산 기반 진단검사 원리와 절차를 이해하는 역량
병리검체 전처리|병리 검체 처리의 기본 절차를 이해하는 역량
검사실 정도관리|검사 정확성과 재현성을 관리하는 역량
검사 장비 운용|임상검사 장비를 운용하고 점검하는 역량
검사 결과 품질검토|검사 결과의 오류와 품질 지표를 검토하는 역량
""",

    "MEDICAL_IMAGING": """
일반 방사선 영상획득|일반 방사선 영상획득 원리와 절차를 적용하는 역량
CT 영상검사 이해|CT 장비와 영상획득 과정을 이해하는 역량
MRI 영상검사 이해|MRI 장비와 영상획득 과정을 이해하는 역량
초음파 영상 원리|초음파 영상 형성 원리를 이해하는 역량
의료영상 품질관리|의료영상과 검사 장비의 품질을 평가하는 역량
방사선 안전관리|방사선 방호와 피폭 감소 원칙을 적용하는 역량
방사선 선량 평가|방사선 선량 관련 지표를 측정·해석하는 역량
영상해부학 해석|의료영상에서 해부학적 구조를 식별하는 역량
디지털 의료영상 처리|의료영상 처리와 보정 기법을 적용하는 역량
의료영상 아카이브 운용|의료영상 저장·조회 시스템을 운용하는 역량
""",

    "OPTOMETRY": """
시력 측정|시력을 표준화된 방법으로 평가하는 역량
굴절검사|안구 굴절상태를 측정·해석하는 역량
양안시 기능평가|양안시와 시기능 상태를 평가하는 역량
콘택트렌즈 피팅|콘택트렌즈의 착용 적합성을 평가하는 역량
안경렌즈 가공|안경렌즈 조제가공과 품질 확인 역량
안경테 피팅|안경테의 착용 상태와 조정을 평가하는 역량
안광학 기기 운용|안광학 측정·조제 기기를 운용하는 역량
시기능 검사 결과 해석|시기능 검사결과의 의미를 설명하는 역량
""",

    "DENTAL_HYGIENE": """
구강건강 상태평가|구강위생과 구강건강 상태를 평가하는 역량
구강보건 교육|구강질환 예방과 관리 방법을 교육하는 역량
예방치과 관리|예방치과 관리 활동과 절차를 이해하는 역량
치면세마 실무|치면세마와 기구 활용 원칙을 적용하는 역량
치과 감염관리|치과 진료 환경의 감염관리 원칙을 적용하는 역량
치과진료 보조|치과진료 보조 업무와 협업 절차를 이해하는 역량
치주건강 관리|치주 상태와 예방적 관리를 이해하는 역량
치과 의료기구 관리|치과 기구의 세척·소독·멸균을 관리하는 역량
""",

    "EMERGENCY_CARE": """
응급환자 초기평가|응급환자의 의식·호흡·순환을 신속히 평가하는 역량
응급 중증도 분류|응급환자 우선순위 분류 원칙을 적용하는 역량
기도유지 및 환기 보조|기도유지와 환기 보조 원칙을 적용하는 역량
기본심폐소생술|기본소생술 절차와 원칙을 적용하는 역량
자동심장충격기 적용|자동심장충격기의 사용과 안전 원칙을 적용하는 역량
외상환자 평가|외상 환자의 주요 손상과 위험을 평가하는 역량
응급환자 이송 및 고정|환자 이동과 고정 원칙을 적용하는 역량
응급현장 의사소통|응급현장 팀 협력과 환자 인계를 수행하는 역량
""",

    "HOSPITAL_ADMIN": """
병원 원무관리|접수·수납·진료지원 행정 절차를 수행하는 역량
진료비 청구관리|진료비 청구 자료와 절차를 처리하는 역량
건강보험 수가 이해|건강보험 수가체계를 이해하는 역량
병원 회계관리|병원의 재무·회계 자료를 처리하는 역량
의료기관 인적자원관리|의료기관 인력 운영 원칙을 이해하는 역량
보건의료 통계관리|보건의료 운영 지표를 집계·분석하는 역량
병원 서비스 운영분석|의료서비스 운영 지표를 분석하는 역량
의료기관 인증 준비|의료기관 인증평가 기준과 절차를 이해하는 역량
의료기관 고객경험 관리|환자의 서비스 경험을 조사·개선하는 역량
보건의료 정책분석|보건의료 정책의 영향과 수단을 분석하는 역량
""",

    "BIOMEDICAL_ENGINEERING": """
의료기기 요구사항 분석|의료기기 개발 요구사항을 정의하는 역량
의료기기 전자회로 설계|의료기기 전자회로를 설계·시험하는 역량
생체신호 계측|생체신호를 획득하고 특성을 평가하는 역량
의료기기 시험평가|의료기기 성능·안전 시험을 수행하는 역량
의료기기 위험관리|의료기기의 위험을 식별·평가·통제하는 역량
의료기기 유지보수|의료기기를 점검·유지보수하는 역량
의료기기 규제요건 이해|의료기기 인허가 요구사항을 이해하는 역량
의료기기 사용적합성 평가|의료기기 사용성과 사용오류 위험을 평가하는 역량
의료기기 품질관리|의료기기 제조·검사 품질관리 역량
의료기기 임베디드 개발|의료기기용 임베디드 SW 개발 역량
""",

    "HEALTH_IT": """
의료정보시스템 분석|의료정보시스템 업무·기능 요구사항을 분석하는 역량
전자의무기록 시스템 운용|EMR 기능과 진료정보 처리 흐름을 이해하는 역량
HL7 FHIR 연계|FHIR를 활용한 의료정보 교환 구조를 적용하는 역량
DICOM 데이터 처리|DICOM 의료영상 데이터와 메타데이터를 처리하는 역량
PACS 연동|의료영상 시스템의 연동 구조를 이해하는 역량
의료데이터 표준화|의료데이터 표현·코드·상호운용성 규칙을 적용하는 역량
의료정보 보안설계|의료정보시스템 보안 요구사항을 설계하는 역량
디지털 헬스케어 서비스 기획|디지털 헬스케어 서비스 요구사항을 정의하는 역량
의료 API 설계|의료데이터 교환을 위한 API를 설계하는 역량
""",

    "HEALTH_DATA_AI": """
임상데이터 정제|임상 기록과 검사 데이터를 정제하는 역량
임상데이터 품질관리|의료데이터의 누락·오류를 검증하는 역량
임상통계 분석|임상 데이터에 통계 분석을 적용하는 역량
생존분석|시간-사건 자료의 생존분석을 수행하는 역량
의료영상 데이터 라벨링|의료영상 학습데이터 어노테이션 역량
의료 AI 성능평가|의료 AI 모델의 성능과 일반화를 평가하는 역량
의료데이터 비식별화|식별 위험을 낮추는 데이터 처리 역량
임상연구 데이터 관리|임상연구 데이터의 입력·검증·관리를 수행하는 역량
의료데이터 시각화|임상·보건 데이터를 시각화하는 역량
의료 AI 편향평가|의료 AI의 성능 편차와 편향을 평가하는 역량
""",

    "BIOMATERIALS": """
생체재료 특성평가|생체재료의 물리·화학적 특성을 평가하는 역량
고분자 소재 분석|고분자 구조와 물성을 분석하는 역량
재료 표면분석|재료 표면의 특성과 구조를 분석하는 역량
의료용 코팅 기술|의료재료 코팅 원리와 기능을 적용하는 역량
생체적합성 시험 이해|생체적합성 시험 목적과 결과를 이해하는 역량
바이오소재 제조공정|바이오소재 제조공정과 품질 변수를 이해하는 역량
의료신소재 품질평가|의료 신소재의 성능과 품질을 평가하는 역량
신소재 물성평가|소재의 물리적·기계적 특성을 측정하는 역량
""",

    "PHARMA_BIOTECH": """
세포배양 실무|세포배양의 기본 실험 절차를 수행하는 역량
미생물 배양실무|미생물 배양과 품질관리 역량
PCR 실험|중합효소연쇄반응의 원리와 실험 절차를 적용하는 역량
단백질 분석|단백질 시료를 분석하고 결과를 해석하는 역량
분자생물학 실험|기본 분자생물학 실험 기법을 적용하는 역량
바이오공정 설계|바이오 생산공정과 주요 변수를 설계하는 역량
GMP 문서관리|의약품 제조·품질관리 문서의 관리 역량
의약품 품질시험|의약품 품질시험과 분석 절차를 이해하는 역량
제약공정 밸리데이션 이해|제약공정 검증 요구사항을 이해하는 역량
실험 분석기기 운용|실험 분석장비의 운용·유지관리 역량
""",

    "HEALTHCARE_DESIGN": """
병원 공간기획|의료시설의 업무 흐름을 고려하여 공간을 기획하는 역량
의료시설 동선설계|환자·의료진·물류 동선을 설계하는 역량
실내건축 도면작성|실내건축 설계 도면을 작성하는 역량
CAD 설계|컴퓨터 지원 설계로 도면을 작성하는 역량
BIM 모델링|건물정보모델링으로 공간·시설 정보를 표현하는 역량
의료시설 접근성 설계|다양한 사용자의 접근 요구를 고려하는 역량
의료공간 사용자 조사|의료공간 사용자 요구를 조사·분석하는 역량
의료시설 설비 협업|의료시설의 건축·설비 설계 협업 역량
""",
}


# 의미가 명확한 별칭만 등록
SKILL_ALIASES = {
    "기본심폐소생술": (
        "기본소생술",
        "Basic Life Support",
    ),
    "전자의무기록 시스템 운용": (
        "EMR 운용",
        "EMR 시스템 운용",
    ),
    "HL7 FHIR 연계": (
        "FHIR 연계",
        "HL7 FHIR",
    ),
    "DICOM 데이터 처리": (
        "DICOM 처리",
    ),
    "PACS 연동": (
        "PACS 시스템 연동",
    ),
    "PCR 실험": (
        "중합효소연쇄반응 실험",
    ),
    "CAD 설계": (
        "컴퓨터 지원 설계",
    ),
    "BIM 모델링": (
        "건물정보모델링",
    ),
    "임상데이터 정제": (
        "임상 데이터 전처리",
    ),
}


@dataclass(frozen=True)
class SkillSpec:
    name: str
    category: str
    description: str
    aliases: tuple[str, ...]


def parse_catalog() -> list[SkillSpec]:

    result = []
    owners = {}

    for category, body in SKILL_GROUPS.items():

        for raw_line in body.strip().splitlines():

            if raw_line.count("|") != 1:
                raise ValueError(
                    f"시드 형식 오류: {raw_line}"
                )

            name, description = (
                part.strip()
                for part in raw_line.split("|", 1)
            )

            if not name or not description:
                raise ValueError(
                    f"빈 Skill 필드: {raw_line}"
                )

            aliases = SKILL_ALIASES.get(
                name, ()
            )

            for token in (name, *aliases):

                normalized = normalize_name(token)

                if (
                    not normalized
                    or len(normalized) > 200
                ):
                    raise ValueError(
                        f"이름 길이 오류: {token}"
                    )

                previous = owners.get(normalized)

                if previous is not None:
                    raise ValueError(
                        f"시드 내부 중복: {token} "
                        f"({previous} / {name})"
                    )

                owners[normalized] = name

            result.append(
                SkillSpec(
                    name,
                    category,
                    description,
                    aliases,
                )
            )

    unknown = (
        set(SKILL_ALIASES)
        - {item.name for item in result}
    )

    if unknown:
        raise ValueError(
            f"별칭 대상 Skill 누락: {unknown}"
        )

    return result


def run(*, apply: bool) -> None:

    specs = parse_catalog()

    with Session(engine) as session:

        existing_skills = {
            item.normalized_name: item
            for item in session.scalars(
                select(Skill)
            ).all()
        }

        existing_aliases = {
            item.normalized_alias: item
            for item in session.scalars(
                select(SkillAlias)
            ).all()
        }

        # 모든 충돌을 DB 쓰기 전에 검사
        for item in specs:

            key = normalize_name(item.name)

            skill = existing_skills.get(key)
            reserved = existing_aliases.get(key)

            if (
                reserved is not None
                and (
                    skill is None
                    or reserved.skill_id != skill.skill_id
                )
            ):
                raise RuntimeError(
                    f"기존 별칭과 충돌: {item.name}"
                )

            for alias in item.aliases:

                alias_key = normalize_name(alias)

                name_owner = existing_skills.get(
                    alias_key
                )

                if (
                    name_owner is not None
                    and name_owner.normalized_name != key
                ):
                    raise RuntimeError(
                        f"Skill 이름 충돌: "
                        f"{item.name} -> {alias}"
                    )

                alias_owner = existing_aliases.get(
                    alias_key
                )

                if (
                    alias_owner is not None
                    and (
                        skill is None
                        or alias_owner.skill_id
                        != skill.skill_id
                    )
                ):
                    raise RuntimeError(
                        f"Skill 별칭 충돌: "
                        f"{item.name} -> {alias}"
                    )

        created = 0
        existed = 0
        aliases_added = 0
        categories = {}

        for item in specs:

            key = normalize_name(item.name)

            skill = existing_skills.get(key)

            if skill is None:

                created += 1

                if apply:

                    skill = Skill(
                        name=item.name,
                        normalized_name=key,
                        category=item.category,
                        description=item.description,
                        is_active=True,
                    )

                    session.add(skill)
                    session.flush()

            else:
                existed += 1

                # 기존 Skill 정보는 변경하지 않음

            categories[item.category] = (
                categories.get(item.category, 0) + 1
            )

            for alias in item.aliases:

                alias_key = normalize_name(alias)

                if alias_key in existing_aliases:
                    continue

                aliases_added += 1

                if apply:

                    session.add(
                        SkillAlias(
                            skill_id=skill.skill_id,
                            alias=alias,
                            normalized_alias=alias_key,
                        )
                    )

        mode = "APPLY" if apply else "DRY-RUN"

        print(f"\n[{mode}] Skill 확장")

        print(
            f"시드 후보: {len(specs)}"
        )
        print(
            f"기존 DB Skill: {len(existing_skills)}"
        )
        print(
            f"신규 Skill: {created}"
        )
        print(
            f"중복 Skill: {existed}"
        )
        print(
            f"신규 별칭: {aliases_added}"
        )

        print("\n[분야별 Skill]")

        for category, count in sorted(
            categories.items()
        ):
            print(f"  {category}: {count}")

        if apply:

            session.commit()

            print(
                "\n[COMMITTED] "
                "Skill 적재 완료"
            )

        else:

            print(
                "\n[DRY-RUN] "
                "DB 변경 없음"
            )


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "CareerQuest Medical Skill Seed"
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    args = parser.parse_args()

    run(apply=args.apply)


if __name__ == "__main__":
    main()
