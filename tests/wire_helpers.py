"""Shared fixtures and helpers for the live-server wire tests.

Boots the FastAPI app (with ``/mcp``) under uvicorn in a background thread,
backed by a file SQLite DB and a real API key, and offers the official MCP
client in either protocol era plus raw JSON-RPC helpers.
"""

import base64
import hashlib
import hmac
import json
import socket
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_server.auth.api_key_auth import create_api_key
from api_server.server import app
from common import global_config
from db import engine as db_engine
from db.base import Base


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    # A file DB, not :memory: + StaticPool: the server's auth and quota checks
    # run on several threads at once and each needs its own connection.
    db_file = tmp_path_factory.mktemp("db") / "wire.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng)
    with ExitStack() as stack:
        stack.enter_context(patch.object(db_engine, "_engine", eng))
        stack.enter_context(patch.object(db_engine, "_SessionLocal", session_factory))
        mock_cfg = stack.enter_context(
            patch("api_server.middleware.mcp_auth.global_config")
        )
        mock_cfg.WORKOS_CLIENT_ID = None
        mock_cfg.WORKOS_AUTHKIT_DOMAIN = None
        stack.enter_context(
            patch.object(
                global_config.telephone,
                "transcript_dir",
                str(tmp_path_factory.mktemp("transcripts")),
            )
        )
        with session_factory() as session:
            key, _ = create_api_key(session, user_id="wire-test", scopes=["*"])
        # Bind here and hand uvicorn the open socket, so no other process can
        # take the port between choosing it and the server listening on it.
        sock = stack.enter_context(socket.socket())
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        srv = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(
            target=srv.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 15
        while not srv.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", key
        finally:
            srv.should_exit = True
            thread.join(timeout=15)


def _client(base: str, key: str, mode: str = "auto") -> Client:
    http = httpx2.AsyncClient(headers={"X-API-KEY": key}, timeout=30)
    transport = streamable_http_client(f"{base}/mcp", http_client=http)
    return Client(transport, mode=mode)


ERAS = pytest.mark.parametrize("mode", ["legacy", "auto"], ids=["2025", "2026"])


def _rpc(base: str, key: str, method: str, params: dict) -> dict:
    resp = httpx.post(
        f"{base}/mcp",
        headers={"X-API-KEY": key, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return _json_rpc(resp)


def _rpc_2026(base: str, key: str, method: str, params: dict) -> dict:
    """A request in the 2026-07-28 shape: no session, the envelope in _meta."""
    envelope = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    resp = httpx.post(
        f"{base}/mcp",
        headers={
            "X-API-KEY": key,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
        },
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": method,
            "params": {**params, "_meta": envelope},
        },
        timeout=10,
    )
    return _json_rpc(resp)


def _json_rpc(resp: httpx.Response) -> dict:
    """The JSON-RPC message from a plain JSON or a single-event SSE response."""
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [ln[5:] for ln in resp.text.splitlines() if ln.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def _verify(secret: bytes, headers: dict, body: bytes) -> bool:
    """Independent Standard Webhooks verification (as a subscriber would do)."""
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest())
    return headers["webhook-signature"] == "v1," + expected.decode()
