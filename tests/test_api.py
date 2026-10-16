from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cache_service.api import create_app
from cache_service.database import Payload, Transformation


def test_sample_output_and_identifier_reuse(client, calls):
    data = {
        "list_1": ["first string", "second string", "third string"],
        "list_2": ["other string", "another string", "last string"],
    }
    first = client.post("/payload", json=data)
    assert first.status_code == 201
    assert first.json()["created"] is True
    assert len(first.json()["id"]) == 64
    assert client.get(first.headers["Location"]).json() == {
        "output": (
            "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
        )
    }
    again = client.post("/payload", json=data)
    assert again.status_code == 200
    assert again.json() == {"id": first.json()["id"], "created": False}
    assert sum(calls.values()) == 6


def test_cache_reuses_strings_within_and_across_requests(client, calls):
    client.post("/payload", json={"list_1": ["a", "a"], "list_2": ["a", "b"]})
    response = client.post("/payload", json={"list_1": ["b", "c"], "list_2": ["a", "c"]})
    assert response.status_code == 201
    assert calls == Counter({"a": 1, "b": 1, "c": 1})
    assert client.get(response.headers["Location"]).json() == {"output": "B, A, C, C"}


def test_cache_and_payload_survive_new_app_instance(settings, transformer, calls):
    data = {"list_1": ["persistent"], "list_2": ["cache"]}
    with TestClient(create_app(settings, transformer)) as first:
        identifier = first.post("/payload", json=data).json()["id"]
    with TestClient(create_app(settings, transformer)) as second:
        assert second.get(f"/payload/{identifier}").json() == {"output": "PERSISTENT, CACHE"}
        assert second.post("/payload", json=data).json() == {"id": identifier, "created": False}
        second.post("/payload", json={"list_1": ["cache"], "list_2": ["persistent"]})
    assert calls == Counter({"persistent": 1, "cache": 1})


@pytest.mark.parametrize(
    "data",
    [
        {"list_1": ["a"], "list_2": []},
        {"list_1": [1], "list_2": ["a"]},
        {"list_1": [True], "list_2": ["a"]},
        {"list_1": [None], "list_2": ["a"]},
        {"list_1": "a", "list_2": ["b"]},
        {"list_1": [], "list_2": [], "extra": 1},
        {"list_1": []},
        {"list_1": ["x"] * 1001, "list_2": ["y"] * 1001},
        {"list_1": ["x" * 4097], "list_2": ["y"]},
        None,
    ],
)
def test_invalid_inputs_do_not_call_transformer(client, calls, data):
    assert client.post("/payload", json=data).status_code == 422
    assert not calls


def test_malformed_json(client):
    assert (
        client.post(
            "/payload", content="{bad", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )


def test_missing_and_malformed_ids(client):
    assert client.get("/payload/" + "0" * 64).status_code == 404
    assert client.get("/payload/not-an-id").status_code == 422


def test_empty_lists_and_empty_strings(client, calls):
    empty = client.post("/payload", json={"list_1": [], "list_2": []})
    assert client.get(empty.headers["Location"]).json() == {"output": ""}
    assert client.post("/payload", json={"list_1": [], "list_2": []}).status_code == 200
    strings = client.post("/payload", json={"list_1": ["", ""], "list_2": ["", ""]})
    assert client.get(strings.headers["Location"]).json() == {"output": ", , , "}
    assert calls == Counter({"": 1})


def test_unicode_whitespace_and_exact_inputs(client, calls):
    first = client.post("/payload", json={"list_1": ["straße", " привіт "], "list_2": ["é", "😊"]})
    assert client.get(first.headers["Location"]).json() == {"output": "STRASSE, É,  ПРИВІТ , 😊"}
    second = client.post(
        "/payload", json={"list_1": ["STRASSE", " привіт "], "list_2": ["é", "😊"]}
    )
    assert first.json()["id"] != second.json()["id"]
    assert calls["straße"] == calls["STRASSE"] == 1


def test_order_and_boundaries_are_part_of_identity(client):
    data = [
        {"list_1": ["a", "b"], "list_2": ["c", "d"]},
        {"list_1": ["b", "a"], "list_2": ["d", "c"]},
        {"list_1": ["a, c"], "list_2": ["b, d"]},
    ]
    ids = [client.post("/payload", json=item).json()["id"] for item in data]
    assert len(set(ids)) == 3


def test_large_input_uses_chunked_cache_lookup(client, calls):
    data = {"list_1": [f"a{i}" for i in range(1000)], "list_2": [f"b{i}" for i in range(1000)]}
    response = client.post("/payload", json=data)
    assert response.status_code == 201
    swapped = {"list_1": data["list_2"], "list_2": data["list_1"]}
    assert client.post("/payload", json=swapped).status_code == 201
    assert len(calls) == 2000
    assert set(calls.values()) == {1}


def test_concurrent_requests_across_independent_engines(settings, transformer, calls):
    apps = [create_app(settings, transformer) for _ in range(2)]
    barrier = Barrier(8)
    with TestClient(apps[0]) as first, TestClient(apps[1]) as second:

        def send(index):
            barrier.wait(timeout=10)
            client = first if index % 2 == 0 else second
            return client.post("/payload", json={"list_1": ["shared"] * 3, "list_2": ["other"] * 3})

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(send, range(8)))
        assert sorted(response.status_code for response in responses) == [200] * 7 + [201]
        assert len({response.json()["id"] for response in responses}) == 1
        with Session(apps[0].state.engine) as session:
            assert session.scalar(select(func.count()).select_from(Payload)) == 1
            assert session.scalar(select(func.count()).select_from(Transformation)) == 2
    assert calls == Counter({"shared": 1, "other": 1})


def test_transformer_failure_rolls_back_entire_request(settings):
    transformer = Mock(side_effect=["A", RuntimeError("upstream failed"), "A", "B"])
    app = create_app(settings, transformer)
    with TestClient(app, raise_server_exceptions=False) as client:
        data = {"list_1": ["a"], "list_2": ["b"]}
        assert client.post("/payload", json=data).status_code == 500
        with Session(app.state.engine) as session:
            assert session.scalar(select(func.count()).select_from(Payload)) == 0
            assert session.scalar(select(func.count()).select_from(Transformation)) == 0
        assert client.post("/payload", json=data).status_code == 201
    assert transformer.call_count == 4


def test_lock_timeout_is_retryable(settings):
    settings.database_timeout = 0.02
    app = create_app(settings)
    with TestClient(app) as client, app.state.engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        response = client.post("/payload", json={"list_1": ["a"], "list_2": ["b"]})
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        connection.rollback()
        assert client.post("/payload", json={"list_1": ["a"], "list_2": ["b"]}).status_code == 201


def test_simultaneous_first_startup_on_fresh_database(settings):
    barrier = Barrier(8)

    def start(_):
        barrier.wait(timeout=10)
        with TestClient(create_app(settings)) as client:
            response = client.post("/payload", json={"list_1": ["a"], "list_2": ["b"]})
            return response.status_code, response.json()["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(start, range(8)))
    assert sorted(status for status, _ in results) == [200] * 7 + [201]
    assert len({identifier for _, identifier in results}) == 1
