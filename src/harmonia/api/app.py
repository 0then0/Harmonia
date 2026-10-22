import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import yaml
from arq.jobs import Job
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from sqlalchemy import text

from harmonia import __version__
from harmonia.domain.compiler import PlaylistConstraints
from harmonia.domain.models import Model, Playlist, Track
from harmonia.infrastructure.config import Settings
from harmonia.infrastructure.logging import configure_logging
from harmonia.infrastructure.runtime import Runtime
from harmonia.infrastructure.store import Conflict
from harmonia.providers.base import ProviderError
from harmonia.providers.spotify import Spotify
from harmonia.providers.yandex import YandexMusic
from harmonia.providers.youtube import YouTubeMusic


class ImportRequest(Model):
    provider: str = Field(pattern="^(spotify|youtube|yandex)$")
    reference: str = Field(min_length=1, max_length=1000)


class CompileRequest(Model):
    constraints: PlaylistConstraints
    playlist_ids: list[str] = Field(min_length=1, max_length=20)


class YAMLRequest(Model):
    yaml: str = Field(max_length=65_536)


class PreviewRequest(Model):
    playlist_id: str
    provider: str = Field(pattern="^(spotify|youtube|yandex)$")


class ExportRequest(Model):
    preview_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class ConfirmRequest(Model):
    source: Track
    provider: str = Field(pattern="^(spotify|youtube|yandex)$")
    target: Track


def create_app(settings: Settings | None = None, runtime=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.require_api_key()
        configure_logging()
        app.state.runtime = runtime or Runtime(settings)
        try:
            if runtime is None:
                await app.state.runtime.start()
            yield
        finally:
            if runtime is None:
                await app.state.runtime.close()

    app = FastAPI(title="Harmonia", version=__version__, lifespan=lifespan)
    auth = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def authenticated(key: Annotated[str | None, Depends(auth)]):
        if not key or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "invalid API key")

    api = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])

    def rt():
        return app.state.runtime

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "resource not found"})

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": __version__}

    @api.get("/ready")
    async def ready():
        async with rt().store.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await rt().redis.ping()
        return {"status": "ready"}

    @api.get("/providers")
    async def providers():
        classes = (Spotify, YouTubeMusic, YandexMusic)
        return [
            {
                "name": p.name,
                "configured": p.name in rt().providers,
                "capabilities": p.capabilities.model_dump(),
                "authentication": {
                    "spotify": "SPOTIFY_ACCESS_TOKEN (OAuth access token)",
                    "youtube": "YOUTUBE_AUTH_FILE (read-only browser.json mount)",
                    "yandex": "YANDEX_TOKEN",
                }[p.name],
            }
            for p in classes
        ]

    @api.get("/providers/{provider}/playlists")
    async def provider_playlists(provider: str):
        return await rt().provider(provider).list_playlists()

    @api.get("/providers/{provider}/search")
    async def provider_search(
        provider: str, q: Annotated[str, Query(min_length=1, max_length=500)]
    ):
        return await rt().provider(provider).search(q)

    @api.get("/playlists")
    async def playlists(
        limit: Annotated[int, Query(ge=1, le=200)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return await rt().store.list_playlists(limit=limit, offset=offset)

    @api.get("/playlist")
    async def playlist(id: str):
        return await rt().store.get_playlist(id)

    @api.post("/playlists", status_code=201)
    async def save_playlist(body: Playlist):
        return {"playlist_id": await rt().store.save_playlist(body)}

    @api.post("/constraints/validate")
    async def validate_yaml(body: YAMLRequest):
        try:
            data = await asyncio.to_thread(yaml.safe_load, body.yaml)
            return PlaylistConstraints.model_validate(data)
        except (ValueError, yaml.YAMLError, RecursionError) as exc:
            message = (
                "; ".join(
                    f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors(include_url=False, include_input=False)
                )
                if isinstance(exc, ValidationError)
                else "invalid YAML constraints"
            )
            raise HTTPException(422, message) from None

    async def enqueue(name, *args):
        job = await rt().redis.enqueue_job(name, *args, _job_id=str(uuid4()))
        return {"job_id": job.job_id}

    @api.post("/jobs/import", status_code=202)
    async def import_playlist(body: ImportRequest):
        rt().provider(body.provider)
        try:
            {"spotify": Spotify, "youtube": YouTubeMusic, "yandex": YandexMusic}[
                body.provider
            ].reference(body.reference)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return await enqueue("import_playlist", body.provider, body.reference)

    @api.post("/jobs/build", status_code=202)
    async def build(body: CompileRequest):
        libraries = [await rt().store.get_playlist(key) for key in body.playlist_ids]
        count = sum(len(p.tracks) for p in libraries)
        if count > 1000:
            raise HTTPException(422, "at most 1000 candidates per compilation")
        return await enqueue(
            "build_playlist", body.constraints.model_dump(mode="json"), body.playlist_ids
        )

    @api.post("/jobs/preview", status_code=202)
    async def preview(body: PreviewRequest):
        rt().provider(body.provider)
        source = await rt().store.get_playlist(body.playlist_id)
        if len(source.tracks) > 200:
            raise HTTPException(422, "at most 200 tracks per transfer preview")
        return await enqueue("preview_playlist", body.playlist_id, body.provider)

    @api.get("/previews/{preview_id}")
    async def get_preview(preview_id: UUID):
        return await rt().store.get_plan(str(preview_id))

    @api.post("/matches/confirm")
    async def confirm(body: ConfirmRequest):
        if not body.target.provider_id(body.provider):
            raise HTTPException(422, "target must contain an available provider ID")
        await rt().store.confirm_mapping(body.source, body.provider, body.target)
        return {"confirmed": True}

    @api.post("/jobs/export", status_code=202)
    async def export(body: ExportRequest):
        plan = await rt().store.get_plan(str(body.preview_id))
        if not plan["ready"]:
            raise HTTPException(
                409,
                "preview contains unresolved matches; confirm mappings and create a new preview",
            )
        return await enqueue("export_playlist", str(body.preview_id), body.idempotency_key)

    @api.get("/jobs/{job_id}")
    async def job_status(job_id: UUID):
        job = Job(str(job_id), rt().redis)
        status = await job.status()
        result = None
        if status == "complete":
            try:
                result = await job.result(timeout=0.1)
            except Exception:
                result = {
                    "ok": False,
                    "error": "worker failed or timed out; inspect operation journal before retrying writes",
                }
        return {"status": status, "result": result}

    @api.get("/metadata/isrc/{isrc}")
    async def isrc_lookup(isrc: str):
        try:
            return await rt().musicbrainz.by_isrc(isrc)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get("/metadata/{entity}/{mbid}")
    async def metadata(entity: str, mbid: UUID):
        try:
            return await rt().musicbrainz.lookup(entity, str(mbid))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    app.include_router(api)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static / "index.html")

    return app


app = create_app()
