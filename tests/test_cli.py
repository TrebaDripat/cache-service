import io
import json

import httpx
import pytest

from cache_service.cli import CliSettings, main

DATA = {"list_1": ["привіт"], "list_2": ["world"]}
RESULT = {"output": "ПРИВІТ, WORLD"}
IDENTIFIER = "a" * 64


@pytest.fixture
def requests(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == DATA
            return httpx.Response(201, json={"id": IDENTIFIER, "created": True})
        assert request.url.path == f"/payload/{IDENTIFIER}"
        return httpx.Response(200, json=RESULT)

    original = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return seen


def test_inline_json_repeat_and_aliases(requests, capsys):
    assert main(["-H", "http://localhost:8000", "-j", json.dumps(DATA), "-r", "3"]) == 0
    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [RESULT] * 3
    assert captured.err == ""
    assert len(requests) == 6
    assert all(request.url.host == "localhost" for request in requests)


def test_input_and_output_files(requests, tmp_path, capsys):
    source = tmp_path / "input.json"
    output = tmp_path / "output.jsonl"
    source.write_text(json.dumps(DATA, ensure_ascii=False), encoding="utf-8")
    assert main(["--input", str(source), "--output", str(output), "--repeat", "2"]) == 0
    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == [
        RESULT
    ] * 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [[], ["--input", "-", "--output", "-"]])
def test_stdin_and_stdout(requests, monkeypatch, capsys, args):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(DATA)))
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == RESULT


@pytest.mark.parametrize(
    "args",
    [
        ["--repeat", "0"],
        ["--repeat", "-1"],
        ["--repeat", "many"],
        ["--repeat", "10001"],
        ["--timeout", "0"],
        ["--timeout", "nan"],
        ["--host", "ftp://localhost"],
        ["--host", "http://localhost/?query=yes"],
        ["--host", "http://user:password@localhost"],
        ["--input", "-", "--json", "{}"],
        ["--json", "{"],
        ["--json", '{"list_1":[1],"list_2":["a"]}'],
        ["--json", '{"list_1":[],"list_2":["a"]}'],
        ["--unknown", "value"],
    ],
)
def test_invalid_arguments_do_not_send_requests(requests, capsys, args):
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid input" in captured.err
    assert not requests


def test_missing_file(requests, tmp_path, capsys):
    assert main(["--input", str(tmp_path / "missing.json")]) == 1
    assert "cache-cli:" in capsys.readouterr().err
    assert not requests


def test_output_failure(requests, tmp_path, capsys):
    assert main(["--json", json.dumps(DATA), "-o", str(tmp_path / "missing" / "out")]) == 1
    assert "cache-cli:" in capsys.readouterr().err
    assert not requests


def test_connection_failure(monkeypatch, capsys):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    original = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert main(["--json", json.dumps(DATA)]) == 1
    assert "connection refused" in capsys.readouterr().err


@pytest.mark.parametrize("status", [404, 503])
def test_http_failure_returns_nonzero(monkeypatch, capsys, status):
    original = httpx.Client
    monkeypatch.setattr(
        "cache_service.cli.httpx.Client",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(status)), **kwargs
        ),
    )
    assert main(["--json", json.dumps(DATA)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(status) in captured.err


def test_help_keeps_standard_short_option(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["-h"])
    assert exc.value.code == 0
    assert "--host" in capsys.readouterr().out


def test_ambient_environment_does_not_override_cli(monkeypatch):
    monkeypatch.setenv("host", "https://unexpected.example")
    monkeypatch.setenv("repeat", "999")
    settings = CliSettings(_cli_parse_args=[])
    assert str(settings.host) == "http://127.0.0.1:8000/"
    assert settings.repeat == 1
