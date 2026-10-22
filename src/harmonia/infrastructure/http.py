import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import httpx

from harmonia.providers.base import ProviderError, ProviderHTTPError


class HTTP:
    def __init__(
        self, client: httpx.AsyncClient, before_request: Callable[[], Awaitable] | None = None
    ):
        self.client = client
        self.before_request = before_request

    async def request(self, method: str, path: str, **kwargs) -> dict:
        # Reads can be replayed. A POST timeout can mean "created successfully".
        attempts = 4 if method == "GET" else 1
        for attempt in range(attempts):
            if self.before_request:
                await self.before_request()
            try:
                response = await self.client.request(method, path, **kwargs)
            except httpx.TransportError:
                if attempt + 1 == attempts:
                    raise ProviderError(
                        "provider transport failed; write outcome may be unknown"
                    ) from None
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                delay = 0.5 * 2**attempt
                try:
                    delay = max(delay, float(response.headers.get("Retry-After", "0")))
                except ValueError:
                    pass
                if delay > 30:
                    raise ProviderError("provider rate limited; try again later")
                await asyncio.sleep(delay)
                continue
            if response.is_error:
                raise ProviderHTTPError(response.status_code)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise ProviderError("provider returned malformed JSON") from None
        raise AssertionError("unreachable")


class RedisCache:
    def __init__(self, redis, namespace: str = "harmonia"):
        self.redis = redis
        self.namespace = namespace

    async def get(self, key: str):
        value = await self.redis.get(f"{self.namespace}:cache:{key}")
        return json.loads(value) if value else None

    async def set(self, key: str, value, ttl: int = 86400):
        await self.redis.set(f"{self.namespace}:cache:{key}", json.dumps(value), ex=ttl)

    async def wait(self, name: str, interval: float = 1.05):
        # Recheck eligibility after every wake. Reserving future slots and then
        # sleeping once would let overdue waiters burst after an event-loop stall.
        script = """
        local now = redis.call('TIME')
        local ms = now[1] * 1000 + math.floor(now[2] / 1000)
        local previous = tonumber(redis.call('GET', KEYS[1]) or '0')
        if previous > ms then return previous - ms end
        redis.call('SET', KEYS[1], ms + ARGV[1], 'PX', 3600000)
        return 0
        """
        while True:
            delay = await self.redis.eval(
                script, 1, f"{self.namespace}:rate:{name}", int(interval * 1000)
            )
            if delay <= 0:
                return
            await asyncio.sleep(delay / 1000)


class MemoryCache:
    """Offline CLI / tests only; server uses Redis for coordination."""

    def __init__(self):
        self.values = {}
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def get(self, key):
        entry = self.values.get(key)
        return entry[1] if entry and entry[0] > time.monotonic() else None

    async def set(self, key, value, ttl=86400):
        self.values[key] = (time.monotonic() + ttl, value)

    async def wait(self, name, interval=1.05):
        async with self.lock:
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + interval
