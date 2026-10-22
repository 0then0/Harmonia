import asyncio
import os
import time
from uuid import uuid4

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from harmonia.application.transfer import export_tracks
from harmonia.domain.models import Playlist
from harmonia.infrastructure.http import RedisCache
from harmonia.infrastructure.jobs import build_playlist
from harmonia.infrastructure.store import Conflict, Store

pytestmark = pytest.mark.integration


@pytest.fixture
def integration_urls():
    database = os.getenv("HARMONIA_TEST_DATABASE_URL")
    redis = os.getenv("HARMONIA_TEST_REDIS_URL")
    if not database or not redis:
        pytest.skip(
            "set HARMONIA_TEST_DATABASE_URL and HARMONIA_TEST_REDIS_URL to disposable services"
        )
    return database, redis


async def test_database_roundtrip_mapping_and_atomic_idempotency(integration_urls, make_track):
    store = Store(integration_urls[0])
    suffix = str(uuid4())
    source = make_track(id="source:" + suffix)
    target = make_track(id="target:" + suffix, provider="spotify")
    playlist = Playlist(id="p:" + suffix, title="Тест / canonical", tracks=(source,))
    try:
        key = await store.save_playlist(playlist)
        assert await store.get_playlist(key) == playlist
        assert any(p["id"] == key for p in await store.list_playlists())
        assert await store.get_mapping(source, "spotify") is None
        await store.confirm_mapping(source, "spotify", target)
        assert await store.get_mapping(source, "spotify") == target
        with pytest.raises(ValueError):
            await store.confirm_mapping(source, "yandex", target)
        op = "op:" + suffix
        results = await asyncio.gather(
            store.claim_operation(op, {"x": 1}),
            store.claim_operation(op, {"x": 1}),
            return_exceptions=True,
        )
        assert results.count(None) == 1
        assert sum(isinstance(r, Conflict) for r in results) == 1
        await store.finish_operation(op, "succeeded", playlist)
        assert await store.claim_operation(op, {"x": 1}) == playlist
        with pytest.raises(Conflict, match="different payload"):
            await store.claim_operation(op, {"x": 2})
        pending = "uncertain:" + suffix
        await store.claim_operation(pending, {})
        await store.finish_operation(pending, "uncertain")
        with pytest.raises(Conflict, match="uncertain"):
            await store.claim_operation(pending, {})
        with pytest.raises(KeyError):
            await store.get_playlist("not-found:" + suffix)
    finally:
        await store.close()


async def test_redis_cache_and_distributed_rate_limit(integration_urls):
    redis = await create_pool(RedisSettings.from_dsn(integration_urls[1]))
    suffix = str(uuid4())
    cache = RedisCache(redis, namespace="test:" + suffix)
    try:
        assert await cache.get("x") is None
        await cache.set("x", {"cached": True}, ttl=2)
        assert await cache.get("x") == {"cached": True}
        start = time.monotonic()
        await asyncio.gather(cache.wait("mb", 0.1), cache.wait("mb", 0.1), cache.wait("mb", 0.1))
        assert time.monotonic() - start >= 0.18
    finally:
        await redis.aclose()


async def test_real_arq_worker_compiles_and_persists(integration_urls, make_track):
    from types import SimpleNamespace

    store = Store(integration_urls[0])
    redis = await create_pool(RedisSettings.from_dsn(integration_urls[1]))
    suffix = str(uuid4())
    queue = "test:queue:" + suffix
    try:
        key = await store.save_playlist(
            Playlist(
                id="lib:" + suffix, title="Worker test", tracks=(make_track(id="t:" + suffix),)
            )
        )
        job = await redis.enqueue_job(
            "build_playlist", {"duration": "10m"}, [key], _job_id=suffix, _queue_name=queue
        )
        worker = Worker(
            [build_playlist],
            redis_pool=redis,
            queue_name=queue,
            burst=True,
            ctx={"runtime": SimpleNamespace(store=store)},
            handle_signals=False,
            max_tries=1,
        )
        await worker.async_run()
        result = await job.result(timeout=1)
        assert result["ok"]
        assert (await store.get_playlist(result["playlist_id"])).tracks
        await worker.close()
    finally:
        await store.close()
        await redis.aclose()


async def test_concurrent_export_only_calls_provider_once(integration_urls, make_track):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    store = Store(integration_urls[0])
    track = make_track(provider="spotify")
    key = "export:" + str(uuid4())
    provider = SimpleNamespace(
        name="spotify",
        create_playlist=AsyncMock(
            return_value=Playlist(id="remote", title="Export", tracks=(track,), provider="spotify")
        ),
    )
    try:
        results = await asyncio.gather(
            *(export_tracks("Export", [track], provider, store, key) for _ in range(3)),
            return_exceptions=True,
        )
        assert any(isinstance(r, Playlist) for r in results)
        assert provider.create_playlist.await_count == 1
        assert (await export_tracks("Export", [track], provider, store, key)).id == "remote"
    finally:
        await store.close()


async def test_worker_cli_processes_job_from_fresh_interpreter(integration_urls, make_track):
    import sys

    store = Store(integration_urls[0])
    redis = await create_pool(RedisSettings.from_dsn(integration_urls[1]))
    process = None
    try:
        suffix = str(uuid4())
        key = await store.save_playlist(
            Playlist(id="bootstrap:" + suffix, title="Worker bootstrap", tracks=(make_track(),))
        )
        job = await redis.enqueue_job("build_playlist", {"duration": "10m"}, [key], _job_id=suffix)
        env = {
            **os.environ,
            "DATABASE_URL": integration_urls[0],
            "REDIS_URL": integration_urls[1],
            "SPOTIFY_ACCESS_TOKEN": "",
            "YOUTUBE_AUTH_FILE": "",
            "YANDEX_TOKEN": "",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "harmonia.infrastructure.jobs",
            "--burst",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, (stdout + stderr).decode()
        result = await job.result(timeout=1)
        assert result["ok"]
        assert (await store.get_playlist(result["playlist_id"])).tracks
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await store.close()
        await redis.aclose()


async def test_rate_limit_rechecks_overdue_waiters(integration_urls):
    redis = await create_pool(RedisSettings.from_dsn(integration_urls[1]))
    cache = RedisCache(redis, namespace="stall:" + str(uuid4()))
    times = []

    async def request_slot():
        await cache.wait("mb", 0.08)
        times.append(time.monotonic())

    try:
        tasks = [asyncio.create_task(request_slot()) for _ in range(3)]
        await asyncio.sleep(0.025)
        # Simulate a process pause / overloaded event loop beyond all old slots.
        time.sleep(0.25)  # noqa: ASYNC251
        await asyncio.gather(*tasks)
        assert all(b - a >= 0.065 for a, b in zip(times, times[1:], strict=False))
    finally:
        await redis.aclose()


async def test_full_length_provider_playlist_id_roundtrips(integration_urls):
    store = Store(integration_urls[0])
    try:
        playlist = Playlist(id=str(uuid4()) + "x" * 464, title="Long ID", provider="spotify")
        key = await store.save_playlist(playlist)
        assert len(key) == 508
        assert await store.get_playlist(key) == playlist
    finally:
        await store.close()
