import argparse
import asyncio
import logging
from uuid import uuid4

from arq.connections import RedisSettings
from arq.worker import check_health, create_worker

from harmonia.application.transfer import TransferPreview, export_preview, preview_transfer
from harmonia.domain.compiler import PlaylistConstraints, compile_playlist
from harmonia.infrastructure.config import Settings
from harmonia.infrastructure.logging import configure_logging
from harmonia.infrastructure.runtime import Runtime
from harmonia.infrastructure.store import Conflict
from harmonia.providers.base import ProviderError

logger = logging.getLogger(__name__)


def failure(event: str, exc: Exception, fallback: str) -> dict:
    # Only our sanitized exceptions may reach a job result. SDK/DB exception
    # messages can contain credentials; unexpected failures retain their type.
    message = str(exc) if isinstance(exc, (ProviderError, Conflict)) else fallback
    logger.error(
        "%s:%s",
        event,
        message if isinstance(exc, (ProviderError, Conflict)) else type(exc).__name__,
    )
    return {"ok": False, "error": message}


async def startup(ctx):
    configure_logging()
    runtime = Runtime(Settings())
    ctx["runtime"] = runtime
    await runtime.start()


async def shutdown(ctx):
    if runtime := ctx.get("runtime"):
        await runtime.close()


async def import_playlist(ctx, provider: str, reference: str):
    runtime = ctx["runtime"]
    try:
        playlist = await runtime.provider(provider).read_playlist(reference)
        key = await runtime.store.save_playlist(playlist)
        logger.info("playlist_imported")
        return {"ok": True, "playlist_id": key, "warnings": playlist.warnings}
    except Exception as exc:
        return failure(
            "import_failed",
            exc,
            "Import failed. Check credentials, reference and provider availability.",
        )


async def build_playlist(ctx, constraints: dict, playlist_ids: list[str]):
    runtime = ctx["runtime"]
    try:
        playlists = [await runtime.store.get_playlist(key) for key in playlist_ids]
        candidates = [t for p in playlists for t in p.tracks]
        result = await asyncio.to_thread(
            compile_playlist, PlaylistConstraints.model_validate(constraints), candidates
        )
        key = await runtime.store.save_playlist(result)
        return {"ok": True, "playlist_id": key, "warnings": result.warnings}
    except Exception as exc:
        return failure(
            "build_failed",
            exc,
            "Compilation failed. Use at most 1000 candidates with known durations.",
        )


async def export_playlist(ctx, plan_id: str, idempotency_key: str):
    runtime = ctx["runtime"]
    try:
        preview = TransferPreview.model_validate(await runtime.store.get_plan(plan_id))
        result = await export_preview(
            preview, runtime.provider(preview.provider), runtime.store, idempotency_key
        )
        key = await runtime.store.save_playlist(result)
        logger.info("playlist_exported")
        return {"ok": True, "playlist_id": key}
    except Exception as exc:
        return failure(
            "export_failed",
            exc,
            "Export not confirmed. Inspect the remote library and operation journal before retrying.",
        )


async def preview_playlist(ctx, playlist_id: str, provider: str):
    runtime = ctx["runtime"]
    try:
        source = await runtime.store.get_playlist(playlist_id)
        preview = await preview_transfer(
            source, runtime.provider(provider), runtime.store, runtime.musicbrainz
        )
        key = str(uuid4())
        await runtime.store.save_plan(key, preview.model_dump(mode="json"))
        return {"ok": True, "preview_id": key}
    except Exception as exc:
        return failure(
            "preview_failed",
            exc,
            "Matching failed. Check provider availability and MusicBrainz rate limits.",
        )


class WorkerSettings:
    functions = [import_playlist, build_playlist, preview_playlist, export_playlist]
    redis_settings = RedisSettings.from_dsn(Settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 4
    job_timeout = 1800
    max_tries = 1
    retry_jobs = False
    keep_result = 86400
    health_check_interval = 10


async def run_worker(*, burst: bool = False):
    # Construct ARQ inside a running loop for compatibility across supported
    # releases and Python versions without implicit event-loop creation.
    worker = create_worker(WorkerSettings, burst=burst)
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        # ARQ cancels its main task on SIGINT/SIGTERM.
        pass
    finally:
        await worker.close()


def main():
    parser = argparse.ArgumentParser(description="Run Harmonia background jobs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--burst", action="store_true", help="process queued jobs and exit")
    mode.add_argument("--check", action="store_true", help="check the worker heartbeat and exit")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check_health(WorkerSettings))
    asyncio.run(run_worker(burst=args.burst))


if __name__ == "__main__":
    main()
