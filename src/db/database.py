import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# .env 파일 불러오기
load_dotenv()

DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))


# PostgreSQL 접속 주소
DATABASE_URL = URL.create(
    drivername="postgresql+psycopg",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database=DB_NAME,
)


# DB 엔진
engine = create_engine(
    DATABASE_URL,
    echo=False,
)


# 이후 DB 작업에 사용할 세션
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


# 이후 테이블 모델들이 상속할 기본 클래스
class Base(DeclarativeBase):
    pass


def test_connection():
    try:
        with engine.begin() as connection:

            # PostgreSQL 연결 확인
            version = connection.execute(
                text("SELECT version();")
            ).scalar()

            # pgvector 확장 활성화
            connection.execute(
                text("CREATE EXTENSION IF NOT EXISTS vector;")
            )

            # pgvector 버전 확인
            vector_version = connection.execute(
                text("""
                    SELECT extversion
                    FROM pg_extension
                    WHERE extname = 'vector';
                """)
            ).scalar()

        print("PostgreSQL 연결 성공")
        print(f"PostgreSQL: {version}")
        print(f"pgvector: {vector_version}")

    except Exception as error:
        print("DB 연결 실패")
        print(error)


if __name__ == "__main__":
    test_connection()