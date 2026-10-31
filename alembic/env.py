from logging.config import fileConfig

from alembic import context

from src.db.database import engine
from src.db.models import Base


# Alembic Config 객체
config = context.config


# alembic.ini의 logging 설정 사용
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# SQLAlchemy models.py의 전체 metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    DB에 직접 연결하지 않고 SQL만 생성하는 모드.
    """
    url = engine.url.render_as_string(
        hide_password=False
    )

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named"
        },
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    실제 PostgreSQL DB에 연결해서 migration을 수행하는 모드.
    """

    with engine.connect() as connection:

        context.configure(
            connection=connection,
            target_metadata=target_metadata,

            # models.py와 DB의 컬럼 타입 차이 감지
            compare_type=True,

            # server_default 차이도 감지
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()