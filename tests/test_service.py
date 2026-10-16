from cache_service.schemas import PayloadInput
from cache_service.service import payload_id, transform


def test_transformer_is_deterministic_uppercase():
    assert transform("a B, c") == "A B, C"
    assert transform("") == ""


def test_canonical_id_ignores_json_key_order():
    first = PayloadInput.model_validate_json('{"list_1":["a"],"list_2":["b"]}')
    second = PayloadInput.model_validate_json('{"list_2": ["b"], "list_1": ["a"]}')
    assert payload_id(first) == payload_id(second)


def test_transformer_version_changes_identity(monkeypatch):
    data = PayloadInput(list_1=["a"], list_2=["b"])
    previous = payload_id(data)
    monkeypatch.setattr("cache_service.service.TRANSFORMER_VERSION", "uppercase-v2")
    assert payload_id(data) != previous
