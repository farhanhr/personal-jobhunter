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
from personal_job_finder_api.api.setup_session import SetupSessionStore
from personal_job_finder_api.api.v1.router import api_router
from personal_job_finder_api.core.config import Settings, settings
from personal_job_finder_api.core.time import utc_now
from personal_job_finder_api.db.engine import create_database_engine
from personal_job_finder_api.db.migrate import run_migrations
from personal_job_finder_api.integrations.google_sheets.client import SheetsTransport
from personal_job_finder_api.integrations.google_sheets.service import (
    build_google_sheets_transport,
)
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
    sync_transport_factory: Callable[[], SheetsTransport] | None = None,
    sync_transport: SheetsTransport | None = None,
    sync_spreadsheet_id: str | None = None,
    issue_bootstrap_token: bool = False,
) -> FastAPI:
    resolved_settings = app_settings or settings

    def _default_transport_factory() -> SheetsTransport:
        """Build the real transport lazily.

        Sync status reads must never require Google credentials, so the transport
        is only constructed when a dispatch actually has work to send.
        """

        return build_google_sheets_transport(resolved_settings)

    if sync_transport is not None:
        fixed_transport = sync_transport

        def _transport_factory() -> SheetsTransport:
            return fixed_transport

    elif sync_transport_factory is not None:
        _transport_factory = sync_transport_factory
    else:
        _transport_factory = _default_transport_factory

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
        app.state.sync_transport_factory = _transport_factory
        app.state.sync_spreadsheet_id = sync_spreadsheet_id

        try:
            recover_interrupted_runs(db_engine, now=clock())
            if issue_bootstrap_token:
                app.state.setup_sessions.issue_bootstrap(
                    now=clock(),
                    data_dir=resolved_settings.data_dir,
                )
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
    app.state.sync_transport_factory = _transport_factory
    app.state.sync_spreadsheet_id = sync_spreadsheet_id
    app.state.clock = clock
    app.state.setup_sessions = SetupSessionStore()
    if engine is not None:
        app.state.engine = engine
    app.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    return app


app = create_app(issue_bootstrap_token=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
