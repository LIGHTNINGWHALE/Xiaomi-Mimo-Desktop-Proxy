"""Local OpenAI-compatible reverse proxy for Xiaomi MiMo Desktop models.

Reuses the logged-in Xiaomi MiMo Desktop session cookies on this machine to
expose models such as mimo-x-pro-preview over a loopback OpenAI-compatible
API for Codex, Claude Code, CC Switch, and similar clients.

Privacy: cookies and the generated API key never leave this machine and must
not be committed to git.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mimo-local-proxy")


def _default_cookie_db() -> str:
    return str(
        Path.home()
        / "Library"
        / "Application Support"
        / "Xiaomi MiMo"
        / "Partitions"
        / "xiaomi-account"
        / "Cookies"
    )


COOKIE_DB = os.environ.get("MIMO_PROXY_COOKIE_DB") or _default_cookie_db()
UPSTREAM = (
    os.environ.get("MIMO_PROXY_UPSTREAM")
    or "https://mimo-server-cn.xiaomimimo.com/api"
).rstrip("/")
MAGIC_PREFIX = (
    "# Memory system\n\n"
    "You have a persistent file-based memory system. Four file types"
)
MODELS = {
    "mimo-x-pro-preview": "MiMo X Pro Preview",
    "mimo-x-flash-preview": "MiMo X Flash Preview",
    "mimo-pro": "MiMo Pro",
    "mimo-flash": "MiMo Flash",
}
DEFAULT_MODEL = os.environ.get("MIMO_PROXY_DEFAULT_MODEL") or "mimo-x-pro-preview"
if DEFAULT_MODEL not in MODELS:
    DEFAULT_MODEL = "mimo-x-pro-preview"
LISTEN_HOST = os.environ.get("MIMO_PROXY_HOST") or "127.0.0.1"
LISTEN_PORT = int(os.environ.get("MIMO_PROXY_PORT") or "18080")
KEY_FILE = Path(
    os.environ.get("MIMO_PROXY_KEY_FILE")
    or (Path(__file__).resolve().parent / ".local-api-key")
)


def load_desktop_cookies() -> dict[str, str]:
    if not Path(COOKIE_DB).exists():
        return {}
    con = sqlite3.connect(f"file:{COOKIE_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name, value FROM cookies WHERE value != ''"
        ).fetchall()
    finally:
        con.close()
    out: dict[str, str] = {}
    for name, value in rows:
        out[name] = value
    return out


class SsoSession:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._ready = False

    async def client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None:
                cookies = load_desktop_cookies()
                if not cookies:
                    raise RuntimeError("No Xiaomi desktop cookies found")
                self._client = httpx.AsyncClient(
                    cookies=cookies,
                    follow_redirects=True,
                    timeout=httpx.Timeout(300.0, connect=15.0),
                )
                await self._warm()
            return self._client

    async def _warm(self) -> None:
        assert self._client is not None
        r = await self._client.get(
            f"{UPSTREAM}/user/xiaomi/me",
            headers={
                "User-Agent": "mimocode/0.1.0",
                "Accept": "application/json",
                "X-Mimo-Source": "mimocode-cli-free",
            },
        )
        if r.status_code != 200:
            text = (await r.aread()).decode("utf-8", "replace")[:200]
            raise RuntimeError(f"Session warm failed: {r.status_code} {text}")
        self._ready = True
        logger.info("Upstream session warmed")

    async def chat_headers(self) -> dict[str, str]:
        await self.client()
        return {
            "Content-Type": "application/json",
            "User-Agent": "mimocode/0.1.0",
            "Accept": "application/json",
            "X-Mimo-Source": "mimocode-cli-free",
        }

    async def recover(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            await self._warm()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def ensure_api_key() -> str:
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = "mimo-local-" + secrets.token_urlsafe(24)
    KEY_FILE.write_text(key + "\n", encoding="utf-8")
    KEY_FILE.chmod(0o600)
    return key


def normalize_body(body: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    model = out.get("model") or DEFAULT_MODEL
    out["model"] = model if model in MODELS else DEFAULT_MODEL
    out.setdefault("stream", False)
    for key in (
        "service_tier",
        "metadata",
        "modalities",
        "audio",
        "response_format",
        "n",
        "logprobs",
        "top_logprobs",
        "logit_bias",
        "user",
    ):
        out.pop(key, None)
    messages = list(out.get("messages") or [])
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content") or ""
        if isinstance(existing, str) and not existing.startswith(
            MAGIC_PREFIX[:20]
        ):
            messages[0] = {
                **messages[0],
                "content": MAGIC_PREFIX + "\n\n" + existing,
            }
    else:
        messages.insert(0, {"role": "system", "content": MAGIC_PREFIX})
    out["messages"] = messages
    return out


def parse_sse_data(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def aggregate(chunks: list[dict[str, Any]], model: str) -> dict[str, Any]:
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"
    created = int(time.time())
    content = ""
    reasoning = ""
    role = "assistant"
    finish_reason = None
    usage = None
    tool_calls: list[dict[str, Any]] = []

    for chunk in chunks:
        if chunk.get("id"):
            completion_id = chunk["id"]
        created = chunk.get("created") or created
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("role"):
                role = delta["role"]
            if isinstance(delta.get("content"), str):
                content += delta["content"]
            if isinstance(delta.get("reasoning_content"), str):
                reasoning += delta["reasoning_content"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", len(tool_calls))
                while len(tool_calls) <= idx:
                    tool_calls.append(
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    )
                existing = tool_calls[idx]
                if tc.get("id"):
                    existing["id"] = tc["id"]
                func = tc.get("function") or {}
                if func.get("name"):
                    existing["function"]["name"] = func["name"]
                if func.get("arguments"):
                    existing["function"]["arguments"] += func["arguments"]
        if chunk.get("usage"):
            usage = chunk["usage"]

    message: dict[str, Any] = {"role": role, "content": content or None}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    resp = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason or "stop",
            }
        ],
    }
    if usage:
        resp["usage"] = usage
    return resp


sso = SsoSession()
API_KEY = ensure_api_key()
app = FastAPI(title="MiMo Local Proxy", version="1.0.0")


async def verify_key(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(token, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await sso.close()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "models": list(MODELS)}


@app.get("/v1/models")
async def list_models(_: None = Depends(verify_key)) -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": now,
                "owned_by": "xiaomi-mimo",
            }
            for mid in MODELS
        ],
    }


@app.post("/v1/chat/completions")
async def chat(
    request: Request,
    _: None = Depends(verify_key),
):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    original_model = body.get("model") or DEFAULT_MODEL
    normalized = normalize_body(body)
    client_stream = bool(normalized.get("stream"))
    # Always ask upstream to stream so usage/chunk shape is uniform.
    normalized["stream"] = True
    normalized.setdefault("stream_options", {"include_usage": True})

    client = await sso.client()
    headers = await sso.chat_headers()
    url = f"{UPSTREAM}/route/chat/completions"

    async def do_post() -> httpx.Response:
        return await client.post(url, json=normalized, headers=headers)

    resp = await do_post()
    if resp.status_code in (401, 403):
        logger.warning("upstream auth failed (%s), re-warming session", resp.status_code)
        await resp.aclose()
        await sso.recover()
        headers = await sso.chat_headers()
        resp = await do_post()

    if resp.status_code != 200:
        text = (await resp.aread()).decode("utf-8", "replace")
        logger.error("upstream %s: %s", resp.status_code, text[:400])
        raise HTTPException(status_code=resp.status_code, detail=text)

    if client_stream:

        async def streamer():
            async for chunk in resp.aiter_bytes():
                if chunk:
                    yield chunk

        return StreamingResponse(
            streamer(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    chunks: list[dict[str, Any]] = []
    async for line in resp.aiter_lines():
        data = parse_sse_data(line)
        if data is not None:
            chunks.append(data)
    if not chunks:
        raise HTTPException(status_code=502, detail="Empty upstream response")
    return JSONResponse(aggregate(chunks, original_model))


def _content_parts_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                ptype = part.get("type")
                if ptype in (None, "text", "input_text", "output_text"):
                    parts.append(str(part.get("text") or ""))
                elif ptype == "input_image":
                    parts.append("[image]")
                elif ptype == "input_file":
                    parts.append(f"[file:{part.get('filename') or 'unknown'}]")
        return "".join(parts)
    return str(content)


def responses_input_to_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert OpenAI Responses API input into chat messages."""
    messages: list[dict[str, Any]] = []

    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    raw = payload.get("input")
    if raw is None:
        raw = payload.get("messages") or []
    if isinstance(raw, str):
        messages.append({"role": "user", "content": raw})
        return messages
    if not isinstance(raw, list):
        messages.append({"role": "user", "content": str(raw)})
        return messages

    for item in raw:
        if isinstance(item, str):
            if item:
                messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        # Plain chat-style message
        if "role" in item and item_type in (None, "message"):
            role = item.get("role") or "user"
            text = _content_parts_to_text(item.get("content", item.get("text", "")))
            if text:
                messages.append({"role": role, "content": text})
            continue

        if item_type == "message":
            role = item.get("role") or "user"
            text = _content_parts_to_text(item.get("content", item.get("text", "")))
            if text:
                messages.append({"role": role, "content": text})
            continue

        if item_type in ("function_call", "custom_tool_call"):
            # Represent tool invocation as an assistant note so context is kept.
            name = item.get("name") or "tool"
            args = item.get("arguments") or item.get("input") or ""
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[tool_call {name}] {args}",
                }
            )
            continue

        if item_type in ("function_call_output", "custom_tool_call_output"):
            output = item.get("output", "")
            if isinstance(output, (dict, list)):
                output = json.dumps(output, ensure_ascii=False)
            if output:
                messages.append(
                    {"role": "user", "content": f"[tool_result]\n{output}"}
                )
            continue

        if item_type == "reasoning":
            # Skip reasoning blobs; they are not valid chat messages.
            continue

        if item_type in ("input_text", "output_text", "text"):
            text = _content_parts_to_text(item.get("text", item.get("content", "")))
            if text:
                messages.append({"role": "user", "content": text})
            continue

        # Fallback: try content if present
        if "content" in item or "text" in item:
            text = _content_parts_to_text(item.get("content", item.get("text", "")))
            if text:
                messages.append({"role": "user", "content": text})

    # Drop empty messages; upstream rejects empty content.
    cleaned = [m for m in messages if (m.get("content") or "").strip()]
    if not cleaned:
        cleaned = [{"role": "user", "content": "hi"}]
    return cleaned


def to_responses_object(chat: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a chat.completion object into a response object."""
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    usage = chat.get("usage") or {}
    resp_id = "resp_" + secrets.token_hex(12)
    return {
        "id": resp_id,
        "object": "response",
        "created_at": chat.get("created") or int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": "msg_" + secrets.token_hex(8),
                "type": "message",
                "status": "completed",
                "role": message.get("role") or "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": content,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


@app.post("/v1/responses")
async def responses(
    request: Request,
    _: None = Depends(verify_key),
):
    """OpenAI Responses API shim over upstream chat completions."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    original_model = body.get("model") or DEFAULT_MODEL
    client_stream = bool(body.get("stream"))
    messages = responses_input_to_messages(body)
    logger.info(
        "responses request model=%s stream=%s messages=%d",
        original_model,
        client_stream,
        len(messages),
    )

    chat_body: dict[str, Any] = {
        "model": original_model,
        "messages": messages,
    }
    max_out = body.get("max_output_tokens")
    if isinstance(max_out, int) and max_out > 0:
        chat_body["max_tokens"] = max_out
    elif isinstance(body.get("max_tokens"), int):
        chat_body["max_tokens"] = body["max_tokens"]

    # Map Responses reasoning.effort -> chat reasoning_effort when present.
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        chat_body["reasoning_effort"] = reasoning["effort"]
    elif isinstance(body.get("reasoning_effort"), str):
        chat_body["reasoning_effort"] = body["reasoning_effort"]

    # Convert Responses tools to chat tools when present.
    if isinstance(body.get("tools"), list) and body["tools"]:
        chat_tools = []
        for tool in body["tools"]:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                chat_tools.append({"type": "function", "function": tool["function"]})
            elif "name" in tool and "parameters" in tool:
                chat_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool["parameters"],
                        },
                    }
                )
        if chat_tools:
            chat_body["tools"] = chat_tools

    normalized = normalize_body(chat_body)
    # Always stream from upstream for uniform aggregation.
    normalized["stream"] = True
    normalized.setdefault("stream_options", {"include_usage": True})

    client = await sso.client()
    headers = await sso.chat_headers()
    url = f"{UPSTREAM}/route/chat/completions"

    async def do_post() -> httpx.Response:
        return await client.post(url, json=normalized, headers=headers)

    resp = await do_post()
    if resp.status_code in (401, 403):
        logger.warning("upstream auth failed (%s), re-warming session", resp.status_code)
        await resp.aclose()
        await sso.recover()
        headers = await sso.chat_headers()
        resp = await do_post()

    if resp.status_code != 200:
        text = (await resp.aread()).decode("utf-8", "replace")
        logger.error(
            "upstream %s for responses: %s body_msgs=%s",
            resp.status_code,
            text[:400],
            json.dumps(messages, ensure_ascii=False)[:400],
        )
        raise HTTPException(status_code=resp.status_code, detail=text)

    resp_id = "resp_" + secrets.token_hex(12)
    msg_id = "msg_" + secrets.token_hex(8)
    created = int(time.time())

    if client_stream:

        async def streamer():
            # response.created
            yield (
                "event: response.created\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.created",
                        "response": {
                            "id": resp_id,
                            "object": "response",
                            "created_at": created,
                            "status": "in_progress",
                            "model": original_model,
                            "output": [],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            # response.output_item.added
            yield (
                "event: response.output_item.added\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {
                            "id": msg_id,
                            "type": "message",
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            # response.content_part.added
            yield (
                "event: response.content_part.added\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.content_part.added",
                        "item_id": msg_id,
                        "output_index": 0,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

            text_buf: list[str] = []
            usage = None
            async for line in resp.aiter_lines():
                data = parse_sse_data(line)
                if not data:
                    continue
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        text_buf.append(piece)
                        yield (
                            "event: response.output_text.delta\n"
                            "data: "
                            + json.dumps(
                                {
                                    "type": "response.output_text.delta",
                                    "item_id": msg_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "delta": piece,
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                if data.get("usage"):
                    usage = data["usage"]

            full_text = "".join(text_buf)
            yield (
                "event: response.output_text.done\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_text.done",
                        "item_id": msg_id,
                        "output_index": 0,
                        "content_index": 0,
                        "text": full_text,
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield (
                "event: response.content_part.done\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.content_part.done",
                        "item_id": msg_id,
                        "output_index": 0,
                        "content_index": 0,
                        "part": {
                            "type": "output_text",
                            "text": full_text,
                            "annotations": [],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield (
                "event: response.output_item.done\n"
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "id": msg_id,
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": full_text,
                                    "annotations": [],
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            final = to_responses_object(
                aggregate(
                    [
                        {
                            "choices": [
                                {
                                    "delta": {"content": full_text, "role": "assistant"},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": usage,
                        }
                    ],
                    original_model,
                ),
                original_model,
            )
            final["id"] = resp_id
            yield (
                "event: response.completed\n"
                "data: "
                + json.dumps(
                    {"type": "response.completed", "response": final},
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        return StreamingResponse(
            streamer(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    chunks: list[dict[str, Any]] = []
    async for line in resp.aiter_lines():
        data = parse_sse_data(line)
        if data is not None:
            chunks.append(data)
    if not chunks:
        raise HTTPException(status_code=502, detail="Empty upstream response")

    chat = aggregate(chunks, original_model)
    result = to_responses_object(chat, original_model)
    result["id"] = resp_id
    return JSONResponse(result)


def main() -> None:
    logger.info("Local API key written to %s", KEY_FILE)
    logger.info("Listening on http://%s:%s", LISTEN_HOST, LISTEN_PORT)
    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT, log_level="info")


if __name__ == "__main__":
    main()
