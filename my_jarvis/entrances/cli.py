"""CLI 入口：jarvis 命令。"""
from __future__ import annotations
import argparse
import asyncio
import json

import httpx

from my_jarvis.gateway.paths import TOKEN_PATH


def _base() -> str:
    return "http://127.0.0.1:8787"


def _token() -> str:
    return TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""


async def _chat_repl() -> None:
    import websockets
    uri = "ws://127.0.0.1:8787/api/ws"
    async with websockets.connect(uri) as ws:
        print("my jarvis · CLI（exit 退出）")
        loop = asyncio.get_event_loop()
        session_id = None

        async def reader():
            nonlocal session_id
            async for raw in ws:
                out = json.loads(raw)
                if out.get("session_id"):
                    session_id = out["session_id"]
                if out.get("type") == "confirm_request":
                    c = out["confirm"]
                    print(f"\n[需要确认 {c['risk_level']}]: {c['summary']}")
                    ans = await loop.run_in_executor(None, input, "批准? [y/N] ")
                    async with httpx.AsyncClient() as client:
                        await client.post(f"{_base()}/api/confirm",
                                          headers={"X-Jarvis-Token": _token()},
                                          json={"action_id": c["action_id"],
                                                "decision": "approve" if ans.strip().lower() == "y" else "reject"})
                else:
                    print(f"\njarvis: {out.get('text', '')}")

        async def writer():
            nonlocal session_id
            while True:
                line = await loop.run_in_executor(None, input)
                if line.strip() in {"exit", "quit"}:
                    await ws.close()
                    return
                await ws.send(json.dumps({"entrance": "cli", "text": line,
                                          "session_id": session_id}))

        await asyncio.gather(reader(), writer())


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    sub = p.add_subparsers(dest="cmd")
    p.add_argument("text", nargs="*", help="单发消息")
    sub.add_parser("status")
    sub.add_parser("confirm")
    sub.add_parser("scripts")
    args = p.parse_args()

    if not args.cmd and not args.text:
        try:
            asyncio.run(_chat_repl())
        except (KeyboardInterrupt, EOFError):
            print()
        return

    async def oneoff() -> None:
        async with httpx.AsyncClient() as client:
            h = {"X-Jarvis-Token": _token()}
            if args.cmd == "status":
                r = await client.get(f"{_base()}/api/status", headers=h)
            elif args.cmd == "confirm":
                r = await client.get(f"{_base()}/api/confirm", headers=h)
            elif args.cmd == "scripts":
                r = await client.get(f"{_base()}/api/scripts", headers=h)
            else:
                r = await client.post(f"{_base()}/api/chat", headers=h,
                                      json={"text": " ".join(args.text), "entrance": "cli"})
            print(json.dumps(r.json(), ensure_ascii=False, indent=2))

    asyncio.run(oneoff())


if __name__ == "__main__":
    main()
