from collections import Counter

import pytest
from fastapi.testclient import TestClient

from cache_service.api import create_app
from cache_service.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "cache.db")


@pytest.fixture
def calls():
    return Counter()


@pytest.fixture
def transformer(calls):
    def counting_transform(value):
        calls[value] += 1
        return value.upper()

    return counting_transform


@pytest.fixture
def client(settings, transformer):
    with TestClient(create_app(settings, transformer)) as client:
        yield client
