"""Real-process HTTP and installed-CLI smoke test, including a server restart."""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

DATA = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
EXPECTED = {
    "output": (
        "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
    )
}


@contextmanager
def server(directory: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    host = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CACHE_DATABASE_PATH": str(directory / "cache.db"), "PYTHONUTF8": "1"}
    with (directory / "server.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "cache_service.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(base_url=host, timeout=1, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Server exited before becoming ready")
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Server startup timed out")
                yield host, client
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def smoke(directory: Path):
    executable = shutil.which("cache-cli")
    assert executable is not None, "Run with uv run so the installed cache-cli is on PATH"
    source = directory / "input.json"
    source.write_text(json.dumps(DATA), encoding="utf-8")
    destination = directory / "output.jsonl"

    with server(directory) as (host, client):
        created = client.post("/payload", json=DATA)
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        assert client.get(f"/payload/{identifier}").json() == EXPECTED
        for args, stdin in [
            (["--input", str(source), "--repeat", "2"], None),
            (["--json", json.dumps(DATA)], None),
            (["--input", "-"], json.dumps(DATA)),
        ]:
            result = subprocess.run(
                [executable, "--host", host, *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            expected_count = 2 if "--repeat" in args else 1
            assert [json.loads(line) for line in result.stdout.splitlines()] == [
                EXPECTED
            ] * expected_count
        subprocess.run(
            [executable, "-H", host, "-i", str(source), "-o", str(destination)],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        assert json.loads(destination.read_text(encoding="utf-8")) == EXPECTED

    with server(directory) as (_, client):
        assert client.get(f"/payload/{identifier}").json() == EXPECTED
        repeated = client.post("/payload", json=DATA)
        assert repeated.status_code == 200
        assert repeated.json() == {"id": identifier, "created": False}

    # sqlite3.Connection's own context manager commits/rolls back but does not close.
    with closing(sqlite3.connect(directory / "cache.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payloads").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM transformations").fetchone()[0] == 6


if __name__ == "__main__":
    with TemporaryDirectory(prefix="cache-service-smoke-") as path:
        directory = Path(path)
        try:
            smoke(directory)
        except Exception:
            log_path = directory / "server.log"
            if log_path.exists():
                print(log_path.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise
    print("LIVE SMOKE PASSED")
