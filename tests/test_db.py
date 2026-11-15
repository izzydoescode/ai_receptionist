import pytest


@pytest.mark.parametrize(
    "source,expected",
    [
        ("postgres://user:dummy@host/db?sslmode=require", "postgresql+psycopg://user:dummy@host/db?sslmode=require"),
        ("postgresql://host/db", "postgresql+psycopg://host/db"),
        ("postgresql+psycopg://host/db", "postgresql+psycopg://host/db"),
        ("sqlite:///./vicecity.db", "sqlite:///./vicecity.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_normalize_database_url(source, expected):
    from bear.db import normalize_database_url

    assert normalize_database_url(source) == expected


@pytest.mark.parametrize("url", ["", "mysql://host/db", "garbage"])
def test_invalid_database_url(url):
    from bear.db import normalize_database_url

    with pytest.raises(ValueError):
        normalize_database_url(url)


def test_sqlite_concurrent_writes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from bear.db import make_engine
    from sqlalchemy import text

    engine = make_engine(f"sqlite:///{tmp_path}/concurrent.db")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE writes (id INTEGER PRIMARY KEY)"))
        barrier = Barrier(10)

        def write(i):
            barrier.wait(timeout=10)
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO writes (id) VALUES (:id)"), {"id": i})

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(write, range(10)))
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT COUNT(*) FROM writes")) == 10
    finally:
        engine.dispose()


def test_missing_schema_explains_migration(make_client):
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        make_client(_schema=False, _seed=False)


def test_connection_error_never_exposes_password(make_client):
    with pytest.raises(Exception) as error:
        make_client(DATABASE_URL="postgresql+psycopg://user:s3cret-pw@127.0.0.1:1/x", _schema=False, _seed=False)
    assert "s3cret-pw" not in str(error.value)


@pytest.mark.parametrize("shop", ["", "Bad", "a/b", "-shop", "a" * 65, "a\n"])
def test_invalid_shop_id(make_client, shop):
    with pytest.raises(RuntimeError):
        make_client(SHOP_ID=shop, _seed=False)


@pytest.mark.parametrize("table", ["appointments", "repair_orders", "tows", "messages", "audit_events"])
def test_any_missing_table_requires_migration(tmp_path, monkeypatch, table):
    from bear.db import create_schema, make_engine, metadata

    from main import create_app

    url = f"sqlite:///{tmp_path}/partial.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TOOL_SECRET", "x" * 40)
    monkeypatch.setenv("SHOP_ID", "vice-city-auto")
    engine = make_engine(url)
    try:
        create_schema(engine)
        metadata.tables[table].drop(engine)
        with pytest.raises(RuntimeError, match="alembic upgrade head") as error:
            create_app()
        assert url not in str(error.value)
    finally:
        engine.dispose()
