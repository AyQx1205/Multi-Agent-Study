from __future__ import annotations

import asyncio
import json

try:
    import websockets
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装：pip install websockets")

URL = "ws://127.0.0.1:8000/ws/s1"

# 服务端判分示例：q-rational_numbers-2 的答案是 7。
# 真实前端应先用 GET /questions?knowledge_id=... 取题，再提交学生输入。
QUESTION_ID = "q-rational_numbers-2"
ANSWER = "7"


async def main() -> None:
    try:
        async with websockets.connect(URL) as ws:
            await ws.send(
                json.dumps(
                    {
                        "action": "submit",
                        "question_id": QUESTION_ID,
                        "answer": ANSWER,
                        "time_spent_seconds": 42,
                    }
                )
            )

            # 事件链长度不固定：持续读到 2 秒内没有新消息为止。
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    print("[no more frames]")
                    break
                frame = json.loads(raw)
                if frame.get("event_type") == "submission.result":
                    print(
                        f"result: correct={frame.get('is_correct')} "
                        f"answer={frame.get('correct_answer')}"
                    )
                    continue
                print(
                    f"{frame.get('event_type', frame):<34} "
                    f"{str(frame.get('data'))[:90]}"
                )
    except OSError as exc:
        raise SystemExit(
            f"连接失败：{exc}\n先确认 uvicorn 已在 8000 端口启动。"
        ) from exc


if __name__ == "__main__":
    asyncio.run(main())