"""Check transport and native error propagation without loading a model."""

import json
import os
import unittest
from unittest.mock import patch

import httpx
from chat_adapter import MAX_WRAPPER_BYTES, install_chat_adapter
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class ChatAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        app = FastAPI()

        @app.post("/v1/systemone")
        async def native(request: Request, authorization=None):
            body = await request.json()
            self.calls.append((request.url.path, authorization, body))
            if body.get("error"):
                raise HTTPException(body["error"], "native error", headers={"Retry-After": "1"})
            return JSONResponse({"answers": {"color": {"choice": "blue"}},
                                 "usage": {"input_tokens": 42, "output_tokens": 0}},
                                headers={"X-Inference-Time-Ms": "25"})

        with patch.dict(os.environ, {"LAYA_API_KEY": "unit-key"}):
            install_chat_adapter(app)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                       base_url="http://test")
        self.body = {"model": "laya-english", "messages": [
            {"role": "user", "content": json.dumps({"state": "sky", "questions": {}})}]}
        self.headers = {"Authorization": "Bearer unit-key"}

    async def asyncTearDown(self):
        await self.client.aclose()

    async def post(self, body):
        return await self.client.post("/v1/chat/completions", json=body, headers=self.headers)

    async def test_authorization_precedes_parsing(self):
        response = await self.client.post("/v1/chat/completions", content=b"invalid JSON")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])

    async def test_native_transport_and_usage(self):
        response = await self.post(self.body | {"response_format": {"type": "json_object"}})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(self.calls, [("/v1/systemone", "Bearer unit-key",
                                      {"state": "sky", "questions": {}})])
        native = json.loads(result["choices"][0]["message"]["content"])
        self.assertEqual(native["answers"]["color"]["choice"], "blue")
        self.assertEqual(result["usage"], {"prompt_tokens": 42, "completion_tokens": 0,
                                         "total_tokens": 42})
        self.assertEqual(response.headers["x-inference-time-ms"], "25")

    async def test_native_bounds_and_busy_response_propagate(self):
        for status in (413, 422, 503):
            body = self.body | {"messages": [{"role": "user",
                                             "content": json.dumps({"error": status})}]}
            response = await self.post(body)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.headers["retry-after"], "1")

    async def test_unsupported_wrappers_never_call_native(self):
        invalid = [{"model": "other"}, {"stream": True}, {"temperature": .5}, {"tools": []},
                   {"messages": []}, {"messages": self.body["messages"] * 2},
                   {"messages": [{"role": "system", "content": "{}"}]},
                   {"messages": [{"role": "user", "content": "not JSON"}]},
                   {"messages": [{"role": "user", "content": []}]},
                   {"response_format": {"type": "json_schema"}}]
        for override in invalid:
            with self.subTest(override=override):
                response = await self.post(self.body | override)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])

    async def test_wrapper_size_bound(self):
        response = await self.client.post("/v1/chat/completions",
                                          content=b"x" * (MAX_WRAPPER_BYTES + 1),
                                          headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
