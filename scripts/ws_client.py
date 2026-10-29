"""登录后通过 WebSocket 提交一道题，打印结果和事件链。

示例：
    python scripts/ws_client.py
    python scripts/ws_client.py --org demo-school --email student@demo.local \
        --password demo-student-123

也可以直接给 token（仍需 learner-id）：
    python scripts/ws_client.py --token "$env:WS_TOKEN" --learner-id <user-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

import httpx

try:
    import websockets
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装：pip install websockets")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--org", default=os.getenv("WS_ORG", ""))
    parser.add_argument("--email", default=os.getenv("WS_EMAIL", ""))
    parser.add_argument("--password", default=os.getenv("WS_PASSWORD", ""))
    parser.add_argument("--token", default="")
    parser.add_argument("--learner-id", default="")
    parser.add_argument("--question-id", default="q-rational_numbers-2")
    parser.add_argument("--answer", default="7")
    parser.add_argument("--idempotency-key", default="")
    return parser.parse_args()


def authenticate(args: argparse.Namespace) -> tuple[str, str]:
    base = args.base_url.rstrip("/")
    try:
        if args.token:
            response = httpx.get(
                f"{base}/auth/me",
                headers={"Authorization": f"Bearer {args.token}"},
                timeout=10,
            )
            response.raise_for_status()
            return args.token, args.learner_id or response.json()["id"]

        if not args.org or not args.email or not args.password:
            raise SystemExit(
                "请提供 --org/--email/--password（或设置 WS_ORG/WS_EMAIL/WS_PASSWORD），"
                "也可以直接传 --token + --learner-id。"
            )
        response = httpx.post(
            f"{base}/auth/login",
            json={
                "org_slug": args.org,
                "email": args.email,
                "password": args.password,
            },
            timeout=10,
        )
        if response.status_code != 200:
            raise SystemExit(f"登录失败 HTTP {response.status_code}: {response.text}")
        body = response.json()
        return body["access_token"], body["user"]["id"]
    except httpx.RequestError as exc:
        raise SystemExit(
            f"无法连接 {base}：{exc}\n先确认 uvicorn 已启动。"
        ) from exc


async def main() -> None:
    args = parse_args()
    token, learner_id = authenticate(args)
    base = args.base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base}/ws/{learner_id}?token={token}"
    idempotency_key = args.idempotency_key or f"ws-{uuid.uuid4().hex}"

    try:
        async with websockets.connect(url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "action": "submit",
                        "idempotency_key": idempotency_key,
                        "question_id": args.question_id,
                        "answer": args.answer,
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
                        f"replayed={frame.get('replayed')} "
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