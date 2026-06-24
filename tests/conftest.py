from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.routers import api_router


@pytest.fixture
def db_session_stub() -> object:
    return object()


@pytest.fixture
def api_app(db_session_stub: object) -> FastAPI:
    app = FastAPI()

    async def override_get_db() -> AsyncGenerator[object, None]:
        yield db_session_stub

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(api_router)
    return app


@pytest.fixture
def client(api_app: FastAPI) -> TestClient:
    with TestClient(api_app) as test_client:
        yield test_client
