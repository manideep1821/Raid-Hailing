import os

import pytest

from app.container import build_container
from app.storage.memory import in_memory_repositories
from tests.support import TEST_CONFIG, FakeClock

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rides:rides@localhost:5433/rides_test")


@pytest.fixture(scope="session")
def pg_pool():
    from psycopg_pool import PoolTimeout

    from app.storage.postgres import apply_schema, open_pool
    try:
        pool = open_pool(TEST_DATABASE_URL, max_size=20)
    except PoolTimeout:
        pytest.skip(f"Postgres not reachable at {TEST_DATABASE_URL} (run `docker compose up -d`)")
    apply_schema(pool, reset=True)  # the test DB is disposable: always match the current schema.sql
    yield pool
    pool.close()


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def repos(request):
    """Every test using this runs twice: against the in-memory store and against Postgres."""
    if request.param == "memory":
        return in_memory_repositories()
    from app.storage.postgres import postgres_repositories
    pool = request.getfixturevalue("pg_pool")
    with pool.connection() as conn:
        conn.execute("TRUNCATE rides, drivers, users, coupons")
    return postgres_repositories(pool)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def c(repos, clock):
    """The fully wired services over `repos`, with the pinned test config and a fake clock."""
    return build_container(TEST_CONFIG, repos, clock=clock)
