from __future__ import annotations

import asyncio
import os
import logging
import json
from pathlib import Path

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.engine import Engine
from app.providers.eastmoney import EastmoneyProvider
from app.providers.simulated import SimulatedProvider
from app.ws import WSManager


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_CONFIG_PATH = (ROOT.parent / "config" / "config.yaml").resolve()
log = logging.getLogger("uvicorn.error")


def _config_path() -> Path:
    p = os.getenv("QUANT_CONFIG")
    if not p:
        return DEFAULT_CONFIG_PATH
    return Path(p).expanduser().resolve()


def create_app() -> FastAPI:
    cfg = load_config(_config_path())
    ws = WSManager()

    # provider
    if cfg.provider.type == "simulated":
        provider = SimulatedProvider(cfg.provider.simulated)
    elif cfg.provider.type == "eastmoney":
        provider = EastmoneyProvider(cfg.provider.eastmoney)
    else:
        raise ValueError(f"Unsupported provider: {cfg.provider.type}")

    engine = Engine(cfg=cfg, provider=provider, ws=ws)

    app = FastAPI(title="Realtime Quant Tool", version="0.1.0")
    app.state.cfg = cfg
    app.state.ws = ws
    app.state.engine = engine

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.on_event("startup")
    async def _startup() -> None:
        engine.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await engine.stop()
        # Close provider client if it supports it
        aclose = getattr(provider, "aclose", None)
        if callable(aclose):
            await aclose()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        # Avoid noisy 404 in logs; real favicon is optional.
        return Response(status_code=204)

    @app.get("/api/snapshot")
    async def snapshot() -> dict:
        return engine.snapshot()

    @app.get("/api/health")
    async def health() -> dict:
        return engine.health()

    @app.get("/api/ws_clients")
    async def ws_clients() -> dict:
        return {"clients": await ws.count()}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await ws.connect(websocket)
        try:
            client = getattr(websocket, "client", None)
            log.info("WS connected: %s (clients=%s)", client, await ws.count())
        except Exception:
            pass
        try:
            # push an immediate snapshot
            await websocket.send_text(
                json.dumps(
                    {"type": "snapshot", "data": engine.snapshot()},
                    ensure_ascii=False,
                    default=str,
                )
            )
            # Keep the connection open; engine broadcasts from background task.
            # We do a receive with timeout to detect disconnects without blocking forever.
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                except asyncio.TimeoutError:
                    # keepalive; nothing to do
                    pass
        except WebSocketDisconnect:
            await ws.disconnect(websocket)
            try:
                client = getattr(websocket, "client", None)
                log.info("WS disconnected: %s (clients=%s)", client, await ws.count())
            except Exception:
                pass
        except Exception:
            await ws.disconnect(websocket)
            try:
                client = getattr(websocket, "client", None)
                log.exception("WS error: %s", client)
            except Exception:
                pass

    return app


app = create_app()

