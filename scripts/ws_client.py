from __future__ import annotations

import asyncio
import json

try:
    import websockets
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装：pip install websockets")

URL = "ws://127.0.0.1:8000/ws/s1"

async def main() -> None:
    async with websockets.connect(URL) as ws:
        await ws.send(
            json.dumps(
                {
                    "action": "submit",
                    "knowledge_id": "arithmetic",
                    "is_correct": True,
                    "time_spent_seconds": 42,
                }
            )
        )
        for _ in range(6):
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            frame = json.loads(raw)
            print(f"{frame.get('event_type', frame):<34} {str(frame.get('data'))[:90]}")


if __name__ == "__main__":
    asyncio.run(main())