from __future__ import annotations

import asyncio
import json

import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        r = await client.get("/api/snapshot")
        r.raise_for_status()
        print("snapshot ok:", r.json().get("symbols"))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(json.dumps({"smoke_error": str(e)}, ensure_ascii=False))

