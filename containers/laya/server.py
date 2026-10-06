"""Serve only the baked English INT8 checkpoint using Laya's native protocol."""

import os
from unittest.mock import patch

import onnxruntime as ort
import torch
from laya import serve
from laya.onnx_agent import ONNXAgent
from laya.router import RouteDecision, Router

MODEL_DIR = "/opt/laya/model"
MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"


def load_agent():
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    options_class = ort.SessionOptions

    def bounded_options():
        options = options_class()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        return options

    # Laya 0.3.28 does not accept SessionOptions. Scope this override to construction.
    with patch.object(ort, "SessionOptions", bounded_options):
        agent = ONNXAgent(MODEL_DIR, onnx_path=f"{MODEL_DIR}/laya.int8.onnx")
    agent.revision = MODEL_REVISION
    return agent


class EnglishRouter(Router):
    def _route(self, state, questions=None, model=None, task=None, lang=None, lang_guess=None):
        if model is not None and self.resolve(model) != "english":
            raise ValueError("This service only serves the English checkpoint")
        if task is not None or lang not in (None, "en", "english") or lang_guess is not None:
            raise ValueError("Use English inputs without task or language routing overrides")
        return RouteDecision(model="english", repo="convaiinnovations/laya",
                             reason="English INT8 deployment", detection=None, workflow=None)

    def load(self, name):
        if self.resolve(name) != "english":
            raise ValueError("This service only serves the English checkpoint")
        return super().load(name)


def create_app():
    if not os.environ.get("LAYA_API_KEY"):
        raise RuntimeError("LAYA_API_KEY must be configured")
    serve.MAX_BODY_BYTES = 65536
    serve.MAX_STATE_CHARS = 8000
    serve.MAX_QUESTIONS = 4
    serve.MAX_CHOICE_OPTIONS = 20
    serve.MAX_SCORE_LEVELS = 20
    serve.MAX_TOTAL_OPTIONS = 80
    agent = load_agent()
    router = EnglishRouter(device="cpu", max_loaded=1, preload=False)
    router.attach("english", agent)
    router.predict("The sky is blue.", {"color": {
        "type": "choice", "instructions": "Choose the sky's color.",
        "criteria": {"blue": None, "red": None}}})
    app = serve.create_app(router=router)
    # No bulk requests: one inference runs at a time, with one queued request.
    app.router.routes[:] = [route for route in app.router.routes
                            if getattr(route, "path", None) != "/v1/systemone/batch"]
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000, workers=1, access_log=False)
