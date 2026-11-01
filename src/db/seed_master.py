from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.database import engine
from src.db.models import (
    JobRole,
    Skill,
    SkillAlias,
)


# ============================================================
# 문자열 정규화
# ============================================================

def normalize_name(value: str) -> str:
    """
    Skill/별칭/직무명을 DB 비교용 문자열로 정규화.
    지나친 가공은 하지 않고:
    - 앞뒤 공백 제거
    - 연속 공백 하나로
    - 영문 소문자화
    """
    value = value.strip()
    value = re.sub(r"\s+", " ", value)
    return value.lower()


# ============================================================
# CareerQuest 기본 Skill 사전
# ============================================================

SKILLS = [
    # -------------------------
    # Programming
    # -------------------------
    {
        "name": "Python",
        "category": "PROGRAMMING",
        "description": "Python 프로그래밍 언어 활용 능력",
        "aliases": [
            "파이썬",
        ],
    },
    {
        "name": "Java",
        "category": "PROGRAMMING",
        "description": "Java 프로그래밍 언어 활용 능력",
        "aliases": [
            "자바",
        ],
    },
    {
        "name": "JavaScript",
        "category": "PROGRAMMING",
        "description": "JavaScript 프로그래밍 및 웹 개발 능력",
        "aliases": [
            "자바스크립트",
            "JS",
        ],
    },
    {
        "name": "TypeScript",
        "category": "PROGRAMMING",
        "description": "TypeScript 기반 애플리케이션 개발 능력",
        "aliases": [
            "타입스크립트",
            "TS",
        ],
    },
    {
        "name": "C",
        "category": "PROGRAMMING",
        "description": "C 프로그래밍 언어 활용 능력",
        "aliases": [],
    },
    {
        "name": "C++",
        "category": "PROGRAMMING",
        "description": "C++ 프로그래밍 언어 활용 능력",
        "aliases": [
            "CPP",
        ],
    },
    {
        "name": "SQL",
        "category": "PROGRAMMING",
        "description": "SQL을 이용한 데이터 조회 및 조작 능력",
        "aliases": [
            "Structured Query Language",
        ],
    },

    # -------------------------
    # Backend / Web
    # -------------------------
    {
        "name": "Backend Development",
        "category": "BACKEND",
        "description": "서버 및 백엔드 애플리케이션 개발 능력",
        "aliases": [
            "백엔드",
            "백엔드 개발",
            "서버 개발",
        ],
    },
    {
        "name": "Spring",
        "category": "BACKEND",
        "description": "Spring Framework 기반 Java 서버 개발 능력",
        "aliases": [
            "Spring Boot",
            "스프링",
            "스프링부트",
            "스프링 부트",
        ],
    },
    {
        "name": "FastAPI",
        "category": "BACKEND",
        "description": "FastAPI 기반 Python API 서버 개발 능력",
        "aliases": [
            "Fast API",
        ],
    },
    {
        "name": "REST API",
        "category": "BACKEND",
        "description": "REST 구조의 API 설계 및 구현 능력",
        "aliases": [
            "RESTful API",
            "RESTful",
            "REST",
        ],
    },
    {
        "name": "Node.js",
        "category": "BACKEND",
        "description": "Node.js 기반 서버 개발 능력",
        "aliases": [
            "NodeJS",
            "Node",
        ],
    },
    {
        "name": "React",
        "category": "FRONTEND",
        "description": "React 기반 프론트엔드 개발 능력",
        "aliases": [
            "React.js",
            "ReactJS",
            "리액트",
        ],
    },

    # -------------------------
    # Database / Data
    # -------------------------
    {
        "name": "Database",
        "category": "DATA",
        "description": "데이터베이스 기본 원리 및 활용 능력",
        "aliases": [
            "DB",
            "데이터베이스",
        ],
    },
    {
        "name": "RDBMS",
        "category": "DATA",
        "description": "관계형 데이터베이스 이해 및 활용 능력",
        "aliases": [
            "관계형 데이터베이스",
            "Relational Database",
        ],
    },
    {
        "name": "PostgreSQL",
        "category": "DATA",
        "description": "PostgreSQL 데이터베이스 활용 능력",
        "aliases": [
            "Postgres",
        ],
    },
    {
        "name": "Data Modeling",
        "category": "DATA",
        "description": "데이터 모델 및 데이터베이스 구조 설계 능력",
        "aliases": [
            "데이터 모델링",
            "데이터모델링",
        ],
    },
    {
        "name": "Data Analysis",
        "category": "DATA",
        "description": "데이터 탐색, 처리 및 분석 능력",
        "aliases": [
            "데이터 분석",
            "데이터분석",
        ],
    },
    {
        "name": "Data Visualization",
        "category": "DATA",
        "description": "데이터를 시각적으로 표현하고 해석하는 능력",
        "aliases": [
            "데이터 시각화",
            "시각화",
        ],
    },
    {
        "name": "ETL",
        "category": "DATA",
        "description": "데이터 추출, 변환 및 적재 파이프라인 구축 능력",
        "aliases": [
            "Extract Transform Load",
            "데이터 파이프라인",
        ],
    },
    {
        "name": "Statistics",
        "category": "DATA",
        "description": "통계적 분석 및 데이터 해석 능력",
        "aliases": [
            "통계",
            "통계학",
        ],
    },

    # -------------------------
    # AI / Machine Learning
    # -------------------------
    {
        "name": "Machine Learning",
        "category": "AI",
        "description": "머신러닝 모델 설계, 학습 및 평가 능력",
        "aliases": [
            "머신러닝",
            "ML",
        ],
    },
    {
        "name": "Deep Learning",
        "category": "AI",
        "description": "신경망 기반 딥러닝 모델 개발 능력",
        "aliases": [
            "딥러닝",
            "DL",
        ],
    },
    {
        "name": "PyTorch",
        "category": "AI",
        "description": "PyTorch 기반 딥러닝 모델 개발 능력",
        "aliases": [
            "파이토치",
        ],
    },
    {
        "name": "TensorFlow",
        "category": "AI",
        "description": "TensorFlow 기반 머신러닝 및 딥러닝 개발 능력",
        "aliases": [
            "텐서플로",
        ],
    },
    {
        "name": "LLM",
        "category": "AI",
        "description": "대규모 언어 모델 활용 및 응용 기술",
        "aliases": [
            "Large Language Model",
            "Large Language Models",
            "대규모 언어 모델",
            "거대언어모델",
        ],
    },
    {
        "name": "RAG",
        "category": "AI",
        "description": "외부 검색 정보를 결합한 생성형 AI 시스템 구축 기술",
        "aliases": [
            "Retrieval Augmented Generation",
            "검색 증강 생성",
        ],
    },
    {
        "name": "Natural Language Processing",
        "category": "AI",
        "description": "자연어 데이터 처리 및 분석 기술",
        "aliases": [
            "NLP",
            "자연어처리",
            "자연어 처리",
        ],
    },
    {
        "name": "Computer Vision",
        "category": "AI",
        "description": "이미지 및 영상 기반 인공지능 기술",
        "aliases": [
            "CV",
            "컴퓨터 비전",
            "컴퓨터비전",
            "영상처리",
        ],
    },
    {
        "name": "MLOps",
        "category": "AI_INFRA",
        "description": "머신러닝 모델의 배포, 운영 및 자동화 기술",
        "aliases": [
            "ML Ops",
        ],
    },
    {
        "name": "Prompt Engineering",
        "category": "AI",
        "description": "생성형 AI 모델의 프롬프트 설계 및 개선 능력",
        "aliases": [
            "프롬프트 엔지니어링",
        ],
    },

    # -------------------------
    # Infra / DevOps
    # -------------------------
    {
        "name": "Git",
        "category": "DEVOPS",
        "description": "Git 기반 버전 관리 및 협업 능력",
        "aliases": [
            "깃",
        ],
    },
    {
        "name": "Linux",
        "category": "DEVOPS",
        "description": "Linux 환경 활용 및 시스템 운용 능력",
        "aliases": [
            "리눅스",
        ],
    },
    {
        "name": "Docker",
        "category": "DEVOPS",
        "description": "Docker 기반 컨테이너 환경 구축 및 운영 능력",
        "aliases": [
            "도커",
            "Container",
            "컨테이너",
        ],
    },
    {
        "name": "AWS",
        "category": "CLOUD",
        "description": "Amazon Web Services 기반 클라우드 활용 능력",
        "aliases": [
            "Amazon Web Services",
        ],
    },
    {
        "name": "Cloud",
        "category": "CLOUD",
        "description": "클라우드 인프라 및 서비스 활용 능력",
        "aliases": [
            "클라우드",
            "Cloud Computing",
        ],
    },
    {
        "name": "CI/CD",
        "category": "DEVOPS",
        "description": "소프트웨어 빌드, 테스트 및 배포 자동화 능력",
        "aliases": [
            "Continuous Integration",
            "Continuous Deployment",
        ],
    },

    # -------------------------
    # Computer Science
    # -------------------------
    {
        "name": "Algorithm",
        "category": "CS",
        "description": "알고리즘 설계 및 문제 해결 능력",
        "aliases": [
            "알고리즘",
        ],
    },
    {
        "name": "Data Structure",
        "category": "CS",
        "description": "자료구조 이해 및 활용 능력",
        "aliases": [
            "자료구조",
            "Data Structures",
        ],
    },
    {
        "name": "Software Engineering",
        "category": "CS",
        "description": "소프트웨어 개발 생명주기와 설계 원칙에 대한 이해",
        "aliases": [
            "소프트웨어 공학",
            "소프트웨어공학",
        ],
    },
    {
        "name": "Software Testing",
        "category": "CS",
        "description": "소프트웨어 테스트 설계 및 검증 능력",
        "aliases": [
            "테스트",
            "소프트웨어 테스트",
            "Testing",
        ],
    },
    {
        "name": "Operating System",
        "category": "CS",
        "description": "운영체제 구조와 동작 원리에 대한 이해",
        "aliases": [
            "OS",
            "운영체제",
        ],
    },
    {
        "name": "Computer Network",
        "category": "CS",
        "description": "컴퓨터 네트워크 구조와 통신 원리에 대한 이해",
        "aliases": [
            "Network",
            "Networking",
            "네트워크",
        ],
    },

    # -------------------------
    # General capability
    # -------------------------
    {
        "name": "Problem Solving",
        "category": "GENERAL",
        "description": "문제를 분석하고 해결 방법을 도출하는 능력",
        "aliases": [
            "문제 해결",
            "문제해결",
        ],
    },
    {
        "name": "Collaboration",
        "category": "GENERAL",
        "description": "팀 단위 개발 및 협업 능력",
        "aliases": [
            "협업",
            "Teamwork",
            "팀워크",
        ],
    },
]


# ============================================================
# CareerQuest 표준 직무
# ============================================================

JOB_ROLES = [
    {
        "name": "AI Engineer",
        "category": "AI",
        "description": "AI 모델과 AI 기반 서비스를 개발하는 직무",
    },
    {
        "name": "Machine Learning Engineer",
        "category": "AI",
        "description": "머신러닝 모델의 개발, 학습, 배포를 담당하는 직무",
    },
    {
        "name": "Generative AI Engineer",
        "category": "AI",
        "description": "LLM, RAG 등 생성형 AI 서비스를 개발하는 직무",
    },
    {
        "name": "Data Scientist",
        "category": "DATA",
        "description": "데이터를 분석하고 예측 모델을 개발하는 직무",
    },
    {
        "name": "Data Analyst",
        "category": "DATA",
        "description": "데이터 분석을 통해 비즈니스 인사이트를 도출하는 직무",
    },
    {
        "name": "Data Engineer",
        "category": "DATA",
        "description": "데이터 수집, 저장 및 처리 파이프라인을 구축하는 직무",
    },
    {
        "name": "Backend Developer",
        "category": "SOFTWARE",
        "description": "서버와 API 및 백엔드 시스템을 개발하는 직무",
    },
    {
        "name": "MLOps Engineer",
        "category": "AI_INFRA",
        "description": "머신러닝 모델의 배포와 운영 자동화를 담당하는 직무",
    },
]


# ============================================================
# Skill Seed
# ============================================================

def seed_skills(session: Session) -> None:
    print("Skill 기본 사전 입력 시작...")

    created_skills = 0
    created_aliases = 0

    for data in SKILLS:
        normalized_name = normalize_name(
            data["name"]
        )

        skill = session.scalar(
            select(Skill).where(
                Skill.normalized_name
                == normalized_name
            )
        )

        if skill is None:
            skill = Skill(
                name=data["name"],
                normalized_name=normalized_name,
                category=data["category"],
                description=data["description"],
                is_active=True,
            )

            session.add(skill)
            session.flush()

            created_skills += 1

        else:
            # seed 내용이 바뀌었을 때 기본 설명 갱신
            skill.name = data["name"]
            skill.category = data["category"]
            skill.description = data["description"]
            skill.is_active = True

        for alias in data["aliases"]:
            normalized_alias = normalize_name(
                alias
            )

            existing_alias = session.scalar(
                select(SkillAlias).where(
                    SkillAlias.normalized_alias
                    == normalized_alias
                )
            )

            if existing_alias is None:
                session.add(
                    SkillAlias(
                        skill_id=skill.skill_id,
                        alias=alias,
                        normalized_alias=normalized_alias,
                    )
                )

                created_aliases += 1

            elif (
                existing_alias.skill_id
                != skill.skill_id
            ):
                print(
                    "경고: 별칭 충돌 "
                    f"'{alias}' "
                    f"(skill_id="
                    f"{existing_alias.skill_id})"
                )

    print(
        f"Skill 신규 {created_skills}개, "
        f"Alias 신규 {created_aliases}개"
    )


# ============================================================
# Job Role Seed
# ============================================================

def seed_job_roles(
    session: Session,
) -> None:

    print(
        "표준 직무 입력 시작..."
    )

    created = 0

    for data in JOB_ROLES:
        normalized_name = normalize_name(
            data["name"]
        )

        role = session.scalar(
            select(JobRole).where(
                JobRole.normalized_name
                == normalized_name
            )
        )

        if role is None:
            role = JobRole(
                name=data["name"],
                normalized_name=normalized_name,
                category=data["category"],
                description=data["description"],
                is_active=True,
            )

            session.add(role)
            created += 1

        else:
            role.name = data["name"]
            role.category = data["category"]
            role.description = data["description"]
            role.is_active = True

    print(
        f"표준 직무 신규 {created}개"
    )


# ============================================================
# Main
# ============================================================

def main() -> None:

    print("=" * 60)
    print("CareerQuest Master Seed 시작")
    print("=" * 60)

    with Session(engine) as session:
        try:
            seed_skills(session)
            seed_job_roles(session)

            session.commit()

        except Exception:
            session.rollback()
            raise

    print("=" * 60)
    print("CareerQuest Master Seed 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()