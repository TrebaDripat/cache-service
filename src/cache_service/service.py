"""Payload generation and persistent cache coordination."""

import hashlib
import json
from collections.abc import Callable

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from cache_service.database import Payload, Transformation
from cache_service.schemas import PayloadInput

# Increment when changing transformation semantics to avoid serving stale results.
TRANSFORMER_VERSION = "uppercase-v1"


def transform(value: str) -> str:
    """Stand-in for a deterministic external service."""
    return value.upper()


def payload_id(payload: PayloadInput) -> str:
    canonical = json.dumps(
        [TRANSFORMER_VERSION, payload.list_1, payload.list_2],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_payload(engine: Engine, identifier: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Payload, identifier)
        return row.output if row is not None else None


def generate_payload(
    engine: Engine,
    payload: PayloadInput,
    transformer: Callable[[str], str] = transform,
) -> tuple[str, bool]:
    identifier = payload_id(payload)
    if get_payload(engine, identifier) is not None:
        return identifier, False

    with Session(engine) as session:
        # SQLite's database-level writer lock coordinates separate workers too. Recheck
        # after taking it: another request may have filled the cache while we waited.
        session.execute(text("BEGIN IMMEDIATE"))
        try:
            if session.get(Payload, identifier) is not None:
                session.rollback()
                return identifier, False

            sources = list(dict.fromkeys(payload.list_1 + payload.list_2))
            cached: dict[str, str] = {}
            # Keep each IN clause below SQLite's conservative parameter limit.
            for start in range(0, len(sources), 500):
                rows = session.scalars(
                    select(Transformation).where(
                        Transformation.version == TRANSFORMER_VERSION,
                        Transformation.source.in_(sources[start : start + 500]),
                    )
                )
                cached.update((row.source, row.result) for row in rows)

            for source in sources:
                if source not in cached:
                    result = transformer(source)
                    cached[source] = result
                    session.add(
                        Transformation(version=TRANSFORMER_VERSION, source=source, result=result)
                    )

            output = ", ".join(
                cached[value]
                for pair in zip(payload.list_1, payload.list_2, strict=True)
                for value in pair
            )
            session.add(Payload(id=identifier, output=output))
            session.commit()
        except Exception:
            # No partial payloads or partially filled caches survive a failed request.
            session.rollback()
            raise
    return identifier, True
