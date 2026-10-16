"""Transport native Laya decisions through the OSS OpenAI chat route."""

import hmac
import json
import os
import time
import uuid

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

MODEL = "laya-english"
MAX_WRAPPER_BYTES = 131072


def install_chat_adapter(app):
    native_endpoint = next(route.endpoint for route in app.routes
                           if getattr(route, "path", None) == "/v1/systemone")
    expected_auth = ("Bearer " + os.environ["LAYA_API_KEY"]).encode()

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(default=None)):
        if not hmac.compare_digest((authorization or "").encode(), expected_auth):
            raise HTTPException(401, "Invalid API key")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WRAPPER_BYTES:
                raise HTTPException(413, "Chat wrapper exceeds 128 KiB")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("model") != MODEL:
                raise ValueError(f"model must be {MODEL}")
            allowed = {"model", "messages", "stream", "response_format", "user"}
            if set(body) - allowed:
                raise ValueError("Unsupported chat parameter")
            if body.get("stream", False) is not False:
                raise ValueError("Streaming is not supported for decisions")
            if body.get("response_format") not in (None, {"type": "json_object"}):
                raise ValueError("Only json_object response format is supported")
            messages = body["messages"]
            if not isinstance(messages, list) or len(messages) != 1:
                raise ValueError("Send one user message with native JSON")
            message = messages[0]
            if not isinstance(message, dict) or message.get("role") != "user":
                raise ValueError("Send one user message with native JSON")
            if set(message) != {"role", "content"} or not isinstance(message["content"], str):
                raise ValueError("Message content must be a JSON string")
            native = json.loads(message["content"])
            if not isinstance(native, dict):
                raise ValueError("Message content must encode a native decision request object")
            encoded = json.dumps(native).encode()
        except (ValueError, KeyError, TypeError, RecursionError):
            raise HTTPException(422, "Invalid chat wrapper; send one user message with native JSON")

        async def receive():
            return {"type": "http.request", "body": encoded, "more_body": False}

        scope = dict(request.scope, path="/v1/systemone", raw_path=b"/v1/systemone",
                     headers=[(b"content-type", b"application/json"),
                              (b"content-length", str(len(encoded)).encode())])
        # Reuse upstream auth, admission, inference lock, native bounds and error responses.
        response = await native_endpoint(Request(scope, receive), authorization=authorization)
        result = json.loads(response.body)
        usage = result["usage"]
        prompt = usage.get("input_tokens", 0)
        completion = usage.get("output_tokens", 0)
        return JSONResponse({
            "id": "chatcmpl-laya-" + uuid.uuid4().hex,
            "object": "chat.completion", "created": int(time.time()), "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant",
                         "content": json.dumps(result)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion}},
            headers={name: value for name, value in response.headers.items()
                     if name.lower() in ("server-timing", "x-inference-time-ms")})
