import os

import pytest

from app.repository import in_memory_repositories

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rides:rides@localhost:5433/rides_test")


@pytest.fixture(scope="session")
def pg_pool():
    from psycopg_pool import PoolTimeout

    from app.postgres import apply_schema, open_pool
    try:
        pool = open_pool(TEST_DATABASE_URL, max_size=20)
    except PoolTimeout:
        pytest.skip(f"Postgres not reachable at {TEST_DATABASE_URL} (run `docker compose up -d`)")
    apply_schema(pool, reset=True)  # the test DB is disposable: always match the current schema.sql
    yield pool
    pool.close()


@pytest.fixture(params=["memory", "postgres"])
def repos(request):
    if request.param == "memory":
        return in_memory_repositories()
    from app.postgres import postgres_repositories
    pool = request.getfixturevalue("pg_pool")
    with pool.connection() as conn:
        conn.execute("TRUNCATE rides, drivers, users, coupons")
    return postgres_repositories(pool)
