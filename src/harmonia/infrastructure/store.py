import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from harmonia.domain.models import Playlist, Track


class Conflict(ValueError):
    pass


class Base(DeclarativeBase):
    pass


class SavedPlaylist(Base):
    __tablename__ = "playlists"
    id: Mapped[str] = mapped_column(String(600), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Mapping(Base):
    __tablename__ = "mappings"
    source_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    target: Mapped[dict] = mapped_column(JSON)


class Operation(Base):
    __tablename__ = "operations"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Store:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def save_playlist(self, playlist: Playlist) -> str:
        # Provider namespace prevents collisions between equal remote IDs.
        key = f"{playlist.provider}:{playlist.id}" if playlist.provider else playlist.id
        values = dict(
            id=key,
            title=playlist.title,
            payload=playlist.model_dump(mode="json"),
            updated_at=datetime.now(timezone.utc),
        )
        async with self.sessions.begin() as session:
            await session.execute(
                insert(SavedPlaylist)
                .values(**values)
                .on_conflict_do_update(index_elements=[SavedPlaylist.id], set_=values)
            )
        return key

    async def get_playlist(self, key: str) -> Playlist:
        async with self.sessions() as session:
            row = await session.get(SavedPlaylist, key)
            if not row:
                raise KeyError("playlist not found")
            return Playlist.model_validate(row.payload)

    async def list_playlists(self, limit: int = 200, offset: int = 0) -> list[dict]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(SavedPlaylist)
                    .order_by(SavedPlaylist.updated_at.desc(), SavedPlaylist.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "title": row.title,
                    "count": len(row.payload["tracks"]),
                    "provider": row.payload.get("provider"),
                }
                for row in rows
            ]

    async def confirm_mapping(self, source: Track, provider: str, target: Track):
        if not target.provider_id(provider):
            raise ValueError("target must have an available identifier in the selected provider")
        values = dict(source_id=source.id, provider=provider, target=target.model_dump(mode="json"))
        async with self.sessions.begin() as session:
            await session.execute(
                insert(Mapping)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[Mapping.source_id, Mapping.provider],
                    set_={"target": values["target"]},
                )
            )

    async def get_mapping(self, source: Track, provider: str) -> Track | None:
        async with self.sessions() as session:
            row = await session.get(Mapping, (source.id, provider))
            return Track.model_validate(row.target) if row else None

    async def save_plan(self, key: str, payload: dict):
        async with self.sessions.begin() as session:
            session.add(Plan(id=key, payload=payload, created_at=datetime.now(timezone.utc)))

    async def get_plan(self, key: str) -> dict:
        async with self.sessions() as session:
            plan = await session.get(Plan, key)
            if not plan:
                raise KeyError("preview not found")
            return plan.payload

    async def claim_operation(self, key: str, payload: dict) -> Playlist | None:
        if not 1 <= len(key) <= 200:
            raise ValueError("idempotency key must contain 1 to 200 characters")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        async with self.sessions.begin() as session:
            inserted = (
                await session.execute(
                    insert(Operation)
                    .values(
                        key=key,
                        digest=digest,
                        state="pending",
                        result=None,
                        created_at=datetime.now(timezone.utc),
                    )
                    .on_conflict_do_nothing()
                    .returning(Operation.key)
                )
            ).scalar_one_or_none()
            if inserted:
                return None
            operation = await session.get(Operation, key)
            if operation.digest != digest:
                raise Conflict("idempotency key was already used with a different payload")
            if operation.state == "succeeded":
                return Playlist.model_validate(operation.result)
            raise Conflict(
                f"operation is {operation.state}; inspect the remote library before using a new key"
            )

    async def finish_operation(self, key: str, state: str, result: Playlist | None = None):
        async with self.sessions.begin() as session:
            await session.execute(
                update(Operation)
                .where(Operation.key == key)
                .values(state=state, result=result.model_dump(mode="json") if result else None)
            )

    async def close(self):
        await self.engine.dispose()
