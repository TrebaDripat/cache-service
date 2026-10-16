# Payload Cache Service

A FastAPI service that interleaves uppercase strings, caches transformations in SQLite,
and reuses payload identifiers. Includes a Pydantic Settings CLI and Docker packaging.
Python 3.13+, SQLAlchemy 2, Pydantic 2; dependencies are locked in `uv.lock`.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked
uv run uvicorn cache_service.api:create_app --factory --host 127.0.0.1 --port 8000
```

Open [Swagger UI](http://127.0.0.1:8000/docs). The database directory is created at startup.
Importing a module does not create a database or connect to it.

## API

`POST /payload` accepts:

```json
{
  "list_1": ["first string", "second string", "third string"],
  "list_2": ["other string", "another string", "last string"]
}
```

A new payload returns `201 Created`, a `Location: /payload/<id>` header, and:

```json
{"id": "<64-character SHA-256 identifier>", "created": true}
```

Identical input returns `200 OK`, the same ID, and `created: false`.
`GET /payload/<id>` returns:

```json
{"output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

Other statuses: `422` for invalid input/ID, `404` for an unknown valid ID, `503` with
`Retry-After: 1` if the SQLite lock wait times out. `/health` reports process liveness,
not database readiness.

Both lists must contain strings and have equal lengths. Unknown fields and type coercions
are rejected. Limits: 1,000 items per list, 4,096 characters per string. Empty lists produce
`""`. Exact case, whitespace, Unicode, duplicates and empty strings are preserved before
transformation. The comma-separated display output is not CSV and is not reversible.

## CLI

With the server running, open another terminal:

```sh
uv run cache-cli --input examples/input.json
uv run cache-cli --host http://127.0.0.1:8000 -i examples/input.json --repeat 3
uv run cache-cli -i examples/input.json -o result.jsonl
uv run cache-cli --help
```

Inline JSON (Bash or PowerShell 7):

```sh
uv run cache-cli --json '{"list_1":["hello"],"list_2":["world"]}'
```

Stdin on Bash: `cat examples/input.json | uv run cache-cli --input -`.
On PowerShell: `Get-Content -Raw examples/input.json | uv run cache-cli --input -`.

| Flag | Meaning | Default |
| --- | --- | --- |
| `-H`, `--host` | HTTP(S) server URL, optional path prefix | `http://127.0.0.1:8000` |
| `-r`, `--repeat` | Iterations, 1–10,000 | `1` |
| `-i`, `--input` | UTF-8 JSON file or `-` for stdin | stdin |
| `-j`, `--json` | Inline JSON; mutually exclusive with input | unset |
| `-o`, `--output` | Output file or `-` for stdout | stdout |
| `--timeout` | HTTP timeout in seconds, greater than 0, at most 300 | `30` |
| `-h`, `--help` | Show usage | — |

The assignment uses `-h` for both host and help. This implementation keeps `-h` for help
and uses `-H` for host. Pydantic Settings parses and validates the arguments. The CLI shares
the API's payload schema. Ambient environment variables do not override CLI input.

Each iteration sends a POST and a GET, then writes the GET result as one JSON line.
Repeated output uses JSON Lines. Files are overwritten; errors go to stderr. Exit codes:
`0` success, `1` HTTP/file errors, `2` invalid input. If a later iteration fails, earlier
successful lines remain; callers must check the exit code.

## Docker

```sh
docker compose up --build -d
uv run cache-cli -i examples/input.json
docker compose down
```

The image runs as an unprivileged user. Compose publishes only `127.0.0.1:8000` and mounts a
named volume at `/data`. The database survives container replacement and `docker compose down`.
`docker compose down --volumes` deliberately removes persisted data.

Without Compose:

```sh
docker build -t payload-cache-service .
docker run --rm -p 127.0.0.1:8000:8000 -v cache-data:/data payload-cache-service
```

## Service configuration

Pydantic Settings reads these environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CACHE_DATABASE_PATH` | `data/cache.db` | SQLite path, relative to the working directory |
| `CACHE_DATABASE_TIMEOUT` | `30` | SQLite lock wait in seconds, greater than 0, at most 300 |

Docker uses `/data/cache.db`. No credentials or paid services are required.

## Design and request flow

1. `api.py` validates the input with `schemas.py` and calls `service.generate_payload`.
2. `payload_id` hashes canonical JSON of the transformer version and both ordered lists.
   JSON formatting and object-key order do not affect identity.
3. If the complete payload exists, it is returned without taking the write lock or invoking
   the transformer.
4. On a miss, a SQLAlchemy session issues `BEGIN IMMEDIATE`, then checks again: another
   request may have created the payload while this request waited for SQLite's writer lock.
5. Cached results are loaded in chunks. Each distinct missing string is transformed once,
   including duplicates shared across the lists. Cache keys are `(version, exact_string)`.
6. Values are interleaved with `zip`, joined with `", "`, and saved with the transformations
   in one transaction. Any failure rolls back all writes.
7. POST returns the ID after commit. GET reads the stored output without transformation.

Two tables: `transformations(version, source, result)` and `payloads(id, output)`, with
primary-key uniqueness. The engine belongs to the app lifespan; operations use separate
sessions. Synchronous routes run in FastAPI's thread pool.

The transformer is an injectable callable, currently `str.upper`. Bump `TRANSFORMER_VERSION`
when changing its semantics. Payload identity follows the original inputs, so `"a"` and
`"A"` can produce the same output with different IDs. SHA-256 collisions are treated as
negligible for this assessment.

## Trade-offs and assumptions

- SQLite is allowed and self-contained. Its writer lock prevents concurrent cache-fill
  calls across local workers sharing the database. Unrelated misses serialize too. A slow
  external transformer would need per-key coordination with bounded leases and a database
  appropriate to that deployment, rather than holding this lock during network I/O.
- This minimizes successful concurrent calls, not exactly-once external effects. A crash,
  failed commit, or upstream failure can cause an uncommitted call to repeat on retry.
  A real transformer must be deterministic and should support idempotent calls.
- “Payload files” is interpreted as durable JSON documents stored in SQLite; the CLI exports
  files. Separate server-side files would duplicate storage without a specified download
  contract.
- No TTL/eviction is requested, so storage grows with unique inputs. `create_all` initializes
  this first schema; future schema changes should introduce migrations.
- Authentication, public rate limits, transport-level body-size limits and multi-host SQLite
  storage are outside this local assessment. Schema limits apply after JSON parsing and do
  not cap raw HTTP body size.

## Verification

```sh
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/smoke.py
uv build
docker compose config --quiet
```

Tests cover sample output, repeat IDs, overlapping strings, input identity, empty/Unicode
values, validation, chunked lookups, persistence, concurrency through independent engines,
rollback, lock timeout, and CLI parsing/I/O/errors. The smoke test starts real Uvicorn
subprocesses, invokes the installed CLI, restarts the server against the same database,
checks table counts, and cleans up processes and temporary files.

The lockfile's Starlette currently emits a deprecation warning for its HTTPX-based TestClient.
The tests still run; no warning filter or test skip hides this dependency warning.

Local verification on 2026-10-16: 51 tests passed; lint, formatting, real HTTP/CLI smoke,
wheel/sdist build and Compose configuration checks passed. Docker image build and container
runtime were not tested because the local Docker daemon was unavailable.


## References

- [Pydantic Settings CLI](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#command-line-support)
- [SQLAlchemy SQLite transactions](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
- [SQLite isolation and BEGIN IMMEDIATE](https://www.sqlite.org/isolation.html)
