"""
Minimal client for F1's SignalR Core live timing feed (the one F1 moved to in 2025).

Auth: pass an F1 TV subscription token. Without one the connection still works,
but F1 may withhold some topics.
"""
import asyncio
import json

import requests
import websockets

NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate?negotiateVersion=1"
WS_URL = "wss://livetiming.formula1.com/signalrcore"
SEP = "\x1e"  # SignalR record separator


async def stream(topics, on_feed, token=None):
    """Connect, subscribe to topics and call on_feed(topic, data, timestamp) for every update.

    The initial state of each topic (what F1 already has for the session) is delivered
    once with timestamp=None.
    """
    headers = {"User-Agent": "BestHTTP"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = requests.post(NEGOTIATE_URL, headers=headers, timeout=15)
    r.raise_for_status()
    conn_token = r.json()["connectionToken"]
    # Load balancer cookies keep the websocket on the same server as negotiate
    headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in r.cookies.items())

    async with websockets.connect(
        f"{WS_URL}?id={conn_token}", additional_headers=headers, max_size=None
    ) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + SEP)
        await ws.send(json.dumps({
            "type": 1, "invocationId": "0", "target": "Subscribe", "arguments": [topics]
        }) + SEP)

        async for raw in ws:
            for part in raw.split(SEP):
                if not part:
                    continue
                msg = json.loads(part)
                t = msg.get("type")
                if t == 1 and msg.get("target") == "feed":
                    topic, data, ts = msg["arguments"]
                    on_feed(topic, data, ts)
                elif t == 3:  # reply to Subscribe: initial state per topic
                    if msg.get("error"):
                        raise RuntimeError(f"Subscribe failed: {msg['error']}")
                    for topic, data in (msg.get("result") or {}).items():
                        on_feed(topic, data, None)
                elif t == 7:  # server closed the connection
                    raise RuntimeError(f"Server closed connection: {msg.get('error')}")
                elif msg == {}:
                    pass  # handshake ack
