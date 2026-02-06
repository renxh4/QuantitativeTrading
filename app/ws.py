from __future__ import annotations

import asyncio
import json
from typing import Any, Set

from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._clients: Set[WebSocket] = set()

    async def count(self) -> int:
        async with self._lock:
            return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        payload = json.dumps(msg, ensure_ascii=False, default=str)
        async with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        # Send concurrently; remove dead clients
        async def _send_one(c: WebSocket) -> None:
            try:
                await c.send_text(payload)
            except Exception:
                await self.disconnect(c)

        await asyncio.gather(*[_send_one(c) for c in clients])

