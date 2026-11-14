"""FastAPI application entrypoint.

Keep this module small: it wires configuration, lifespan tasks, and routers
together. Business logic belongs in domain/search modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from sqlalchemy import Engine

from personal_job_finder_api.api.errors import install_error_handlers
from personal_job_finder_api.api.v1.router import api_router
from personal_job_finder_api.core.config import Settings, settings
from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.db.engine import create_database_engine
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.search.connectors import (
    ConnectorFactory,
    default_connector_factory,
)
from personal_job_finder_api.search.recovery import recover_interrupted_runs


def create_app(
    *,
    app_settings: Settings | None = None,
    engine: Engine | None = None,
    clock: Callable[[], datetime] = utc_now,
    connector_factory: ConnectorFactory = default_connector_factory,
) -> FastAPI:
    resolved_settings = app_settings or settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_engine = engine is None
        db_engine = engine
        if db_engine is None:
            db_engine = create_database_engine(resolved_settings)
            try:
                run_migrations(resolved_settings.database_url)
            except BaseException:
                db_engine.dispose()
                raise

        app.state.engine = db_engine
        app.state.clock = clock
        app.state.settings = resolved_settings
        app.state.connector_factory = connector_factory

        try:
            recover_interrupted_runs(db_engine, now=clock())
            yield
        finally:
            if owns_engine:
                db_engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.version,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.state.settings = resolved_settings
    app.state.connector_factory = connector_factory
    app.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
