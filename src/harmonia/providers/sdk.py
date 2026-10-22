import asyncio
from collections.abc import Awaitable, Callable

from harmonia.providers.base import ProviderError


async def read_with_retry(call: Callable[[], Awaitable], transient: tuple[type[Exception], ...]):
    for attempt in range(4):
        try:
            return await call()
        except transient:
            if attempt == 3:
                raise ProviderError("provider read failed after retries") from None
            await asyncio.sleep(0.5 * 2**attempt)


async def sdk_write(call: Callable[[], Awaitable]):
    try:
        return await call()
    except Exception:
        # SDK errors can include authorization headers or raw HTTP bodies.
        raise ProviderError(
            "provider write failed; inspect the remote library before retrying"
        ) from None
