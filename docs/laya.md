# Laya English through LiteLLM

Laya is a CPU-only, single-replica decision service in the `litellm` namespace.
It serves the English 421M checkpoint from `convaiinnovations/laya`, pinned to
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, using Laya 0.3.28 and ONNX Runtime
1.30.0. MatMul weights use symmetric block INT8 (32 weights per block);
activations, embeddings and other operators remain floating point. This is not
the upstream dynamic-activation INT8 export, which has documented decision drift.

## Client contract

Send the native Jev-shaped JSON body to
`POST https://ai.noel.fyi/laya/v1/systemone`, using a LiteLLM API key.
LiteLLM validates that key and replaces the upstream authorization header with
the shared `LAYA_API_KEY` from the SOPS-encrypted `laya-runtime` Secret.
The internal Service is not exposed through a separate public hostname.

Example, with the client key supplied through `LITELLM_API_KEY`:

```python
import json
import os
import urllib.request

body = {
    "state": "The sky is blue.",
    "questions": {
        "color": {
            "type": "choice",
            "instructions": "Choose the sky's color.",
            "criteria": {"blue": None, "red": None},
        }
    },
}
request = urllib.request.Request(
    "https://ai.noel.fyi/laya/v1/systemone",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["LITELLM_API_KEY"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=15) as response:
    print(json.load(response))
```

Laya 0.3.28 clamps the checkpoint's temperature for 11 or more choices to its
supported minimum. Treat confidence for that bucket as uncalibrated.

Answers contain the native `choice`, `score` or `noul` result and probabilities,
with token usage. This endpoint uses LiteLLM's authenticated pass-through API;
it is not an OpenAI chat-completions model. Chat model routing, pricing and
model-specific spend enforcement should not be assumed for this custom endpoint.
Existing Qwen chat routes continue to use their own configuration.

## Bounds and capacity

Only English is served. Model overrides for other checkpoints, task-routing and
non-English language overrides are rejected. Text is not translated or checked
for English automatically; callers must provide English input. Batch requests
are disabled. Request limits are 64 KiB JSON, 8,000 state characters, four
questions, 20 choices or score levels per question, and 512 tokens for either
encoder or decision-head overrides. Long inputs may be truncated by Laya; inspect
its usage/truncation fields. LiteLLM's upstream timeout is 10 seconds.

One inference executes at a time using two CPU threads. Two requests may be
admitted (one running, one waiting); excess requests receive 503 with
`Retry-After: 1`. Two admissions are not two parallel inferences. Sustained
throughput is approximately the reciprocal of measured service time; a queued
request adds the preceding request's service time. Measure on Sisyphus before
relying on a latency or throughput SLO. CI timing describes its hosted runner.

The pod requests 250m CPU and 1 GiB RAM, with limits of two CPUs and 2 GiB RAM.
These reservations preserve approximately one CPU and 1 GiB for LiteLLM's
migration Job under the October 6 capacity snapshot. They must be revisited if
other reservations change. CPU requests permit bursts and do not guarantee two
cores under contention. This remains a single-node service. Recreate upgrades
avoid a second resident model and cause a brief outage.

The image embeds weights and tokenizer. Inference has no network egress, no
Kubernetes credentials, a read-only root filesystem, and only a bounded `/tmp`.
Startup preloads and warms the checkpoint before the HTTP server becomes ready.
Cilium permits requests from LiteLLM and host health probes only.

## Image validation and rollout

`Build Laya English INT8` builds on a native AMD64 runner. Before publication it
compares eager FP32 and weight-only INT8 on the same 500 BoolQ validation examples,
seed 42, dataset revision `35b264d03638db9f4ce671b711558bf7ff0f80d5` and the
same 512-token question contract used in the earlier evaluation. Publication
requires FP32 accuracy of at least 80%, INT8 accuracy no more than two percentage
points lower, and at least 97% decision agreement. Probability drift and INT8
latency/RSS are reported, not inferred from the model's size.

This is a BoolQ regression gate, not a new comparison against Jev or proof of
score/noul accuracy or probability calibration on production workloads. Validate
representative application examples before replacing Jev in critical decisions.
The final image contains `/opt/laya/validation.json`; GitHub Actions also uploads
it as `laya-quantization-validation`. FP32 weights and evaluation data are removed
from the final image. The offline HTTP smoke test checks authentication, English
routing, request bounds and refusal of bulk requests under the pod's limits.

Publish an image with the workflow, confirm it is anonymously pullable, and pin
its digest in `laya-deployment.yaml`. Review and merge the GitOps change; Flux
then reconciles the Deployment, encrypted shared key, policy and proxy route.
Do not manually apply these resources. Verify pod readiness, Flux readiness,
missing/invalid client-key rejection, and a successful choice request through
the public LiteLLM route. Recheck node available RAM and errors under bounded
concurrency before increasing admission or token limits.

For rollback, revert the GitOps change through Git and let Flux reconcile.
To rotate the shared upstream key, re-encrypt `laya-runtime.sops.yaml`, then
roll both Laya and LiteLLM through Git-managed pod-template annotations. There
is no persistent application state or model PVC to recover.
