"""Application factory and HTTP boundary."""

import logging
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from cache_service.config import Settings
from cache_service.database import Base, create_database
from cache_service.schemas import PayloadCreated, PayloadInput, PayloadOutput
from cache_service.service import generate_payload, get_payload, transform

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    transformer: Callable[[str], str] = transform,
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_database(settings)
        try:
            # First startup may happen in several worker processes at once. Protect
            # check-then-create DDL with the same database lock as cache population.
            with engine.begin() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                Base.metadata.create_all(connection)
            app.state.engine = engine
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Payload Cache Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(OperationalError)
    async def database_error_handler(request, exc: OperationalError):
        if isinstance(exc.orig, sqlite3.OperationalError) and (
            getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF
        ) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return JSONResponse(
                status_code=503,
                content={"detail": "Database is busy; retry the request"},
                headers={"Retry-After": "1"},
            )
        logger.error("Database operation failed", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Database operation failed"})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/payload", response_model=PayloadCreated, status_code=201)
    def create_payload(payload: PayloadInput, response: Response) -> PayloadCreated:
        identifier, created = generate_payload(app.state.engine, payload, transformer)
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/payload/{identifier}"
        return PayloadCreated(id=identifier, created=created)

    @app.get("/payload/{identifier}", response_model=PayloadOutput)
    def read_payload(
        identifier: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    ) -> PayloadOutput:
        output = get_payload(app.state.engine, identifier)
        if output is None:
            raise HTTPException(status_code=404, detail="Payload not found")
        return PayloadOutput(output=output)

    return app
