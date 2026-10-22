from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from harmonia.api.app import create_app
from harmonia.domain.models import Playlist
from harmonia.infrastructure.config import Settings
from harmonia.infrastructure.jobs import (
    build_playlist,
    export_playlist,
    import_playlist,
    preview_playlist,
)
from harmonia.providers.base import ProviderError

KEY = "test-operator-key-with-32-characters"


@pytest.fixture
def api_runtime():
    store = NS(
        list_playlists=AsyncMock(return_value=[]),
        get_playlist=AsyncMock(return_value=Playlist(id="test", title="Test")),
        save_playlist=AsyncMock(return_value="test"),
        get_plan=AsyncMock(return_value={"ready": False}),
        confirm_mapping=AsyncMock(),
        save_plan=AsyncMock(),
    )
    redis = NS(enqueue_job=AsyncMock(return_value=NS(job_id=str(uuid4()))))
    return NS(
        store=store,
        redis=redis,
        providers={},
        provider=lambda name: (_ for _ in ()).throw(ProviderError("not configured")),
    )


@pytest.fixture
def client(api_runtime):
    with TestClient(create_app(Settings(api_key=KEY), api_runtime)) as client:
        yield client


def test_auth_and_openapi(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/playlists").status_code == 401
    assert client.get("/api/playlists", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/playlists", headers={"X-API-Key": KEY}).json() == []
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
    assert "/api/jobs/export" in schema["paths"]
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_invalid_constraints_and_yaml_safe_loader(client):
    headers = {"X-API-Key": KEY}
    result = client.post(
        "/api/constraints/validate",
        headers=headers,
        json={"yaml": "duration: 90m\nrules:\n  avoid_live: true"},
    )
    assert result.status_code == 200 and result.json()["duration"] == 5400
    for payload in [
        "duration: -1",
        "unknown_field: yes",
        '!!python/object/apply:os.system ["echo UNSAFE"]',
        "not: [valid",
    ]:
        assert (
            client.post(
                "/api/constraints/validate", headers=headers, json={"yaml": payload}
            ).status_code
            == 422
        )


def test_unconfigured_provider_and_unresolved_preview_blocked(client, api_runtime):
    headers = {"X-API-Key": KEY}
    assert (
        client.post(
            "/api/jobs/import", headers=headers, json={"provider": "spotify", "reference": "p1"}
        ).status_code
        == 502
    )
    assert (
        client.post(
            "/api/jobs/export",
            headers=headers,
            json={"preview_id": str(uuid4()), "idempotency_key": "x"},
        ).status_code
        == 409
    )
    api_runtime.redis.enqueue_job.assert_not_awaited()


def test_canonical_roundtrip_and_jobs(client, api_runtime, make_track):
    headers = {"X-API-Key": KEY}
    playlist = Playlist(id="test", title="Тестовая библиотека", tracks=(make_track(),))
    assert (
        client.post(
            "/api/playlists", headers=headers, json=playlist.model_dump(mode="json")
        ).status_code
        == 201
    )
    api_runtime.store.save_playlist.assert_awaited_once()
    result = client.post(
        "/api/jobs/build",
        headers=headers,
        json={"constraints": {"duration": "90m"}, "playlist_ids": ["test"]},
    )
    assert result.status_code == 202 and result.json()["job_id"]
    assert api_runtime.redis.enqueue_job.await_args.args[0] == "build_playlist"


async def test_jobs_import_build_preview_export_flow(make_track):
    source = Playlist(id="test", title="Library", tracks=(make_track(provider="spotify"),))
    provider = NS(
        read_playlist=AsyncMock(return_value=source),
        name="spotify",
        create_playlist=AsyncMock(return_value=source),
    )
    store = NS(
        save_playlist=AsyncMock(return_value="saved"),
        get_playlist=AsyncMock(return_value=source),
        get_mapping=AsyncMock(return_value=None),
        save_plan=AsyncMock(),
        get_plan=AsyncMock(),
        claim_operation=AsyncMock(return_value=None),
        finish_operation=AsyncMock(),
    )
    runtime = NS(store=store, provider=lambda name: provider, musicbrainz=None)
    ctx = {"runtime": runtime}
    assert (await import_playlist(ctx, "spotify", "test"))["ok"]
    assert (await build_playlist(ctx, {"duration": "10m"}, ["test"]))["ok"]
    result = await preview_playlist(ctx, "test", "spotify")
    assert result["ok"]
    store.get_plan.return_value = store.save_plan.await_args.args[1]
    assert (await export_playlist(ctx, result["preview_id"], "export-key"))["ok"]
    provider.create_playlist.assert_awaited_once()


async def test_job_errors_never_return_provider_secrets():
    provider = NS(read_playlist=AsyncMock(side_effect=RuntimeError("secret-token")))
    result = await import_playlist({"runtime": NS(provider=lambda name: provider)}, "spotify", "x")
    assert not result["ok"] and "secret" not in result["error"]


def test_api_requires_strong_key_at_startup(api_runtime):
    with pytest.raises(ValueError, match="HARMONIA_API_KEY"):
        with TestClient(create_app(Settings(api_key=""), api_runtime)):
            pass


def test_non_ascii_invalid_key_is_unauthorized_not_server_error(client):
    assert client.get("/api/playlists", headers={"X-API-Key": b"\xff"}).status_code == 401


def test_playlist_pagination_is_explicit_and_bounded(client, api_runtime):
    response = client.get("/api/playlists?limit=50&offset=200", headers={"X-API-Key": KEY})
    assert response.status_code == 200
    api_runtime.store.list_playlists.assert_awaited_with(limit=50, offset=200)
    assert client.get("/api/playlists?limit=201", headers={"X-API-Key": KEY}).status_code == 422


async def test_job_failure_preserves_safe_provider_status():
    from harmonia.providers.base import ProviderHTTPError

    provider = NS(read_playlist=AsyncMock(side_effect=ProviderHTTPError(403)))
    result = await import_playlist({"runtime": NS(provider=lambda name: provider)}, "spotify", "x")
    assert not result["ok"] and result["error"] == "provider returned HTTP 403"


async def test_worker_startup_failure_still_cleans_runtime(monkeypatch):
    from harmonia.infrastructure import jobs

    runtime = NS(
        start=AsyncMock(side_effect=ValueError("Invalid configuration")), close=AsyncMock()
    )
    monkeypatch.setattr(jobs, "Runtime", lambda settings: runtime)
    ctx = {}
    with pytest.raises(ValueError):
        await jobs.startup(ctx)
    await jobs.shutdown(ctx)
    runtime.close.assert_awaited_once()
