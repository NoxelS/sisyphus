"""Bound activation memory while preserving Laya's native multi-question API."""

from unittest.mock import patch

import numpy as np
import onnxruntime as ort
import uvicorn
from server import create_app


class OneRowSession:
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def run(self, output_names, input_feed, run_options=None):
        rows = input_feed["input_ids"].shape[0]
        if rows == 1:
            return self.session.run(output_names, input_feed, run_options)
        # Every exported input and output has the same leading batch dimension.
        # Keep collation, masks, option ordering, decoding and usage in upstream Laya.
        outputs = [self.session.run(output_names,
                   {name: value[row:row + 1] for name, value in input_feed.items()}, run_options)
                   for row in range(rows)]
        return [np.concatenate(values, axis=0) for values in zip(*outputs)]


def bounded_app():
    session_class = ort.InferenceSession

    def bounded_session(*args, **kwargs):
        options = kwargs["sess_options"]
        # Avoid retaining oversized workspace arenas after a long request.
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        return OneRowSession(session_class(*args, **kwargs))

    # Laya has no session-factory injection; restore ORT immediately after startup.
    with patch.object(ort, "InferenceSession", bounded_session):
        return create_app()


if __name__ == "__main__":
    uvicorn.run(bounded_app(), host="0.0.0.0", port=8000, workers=1, access_log=False)
