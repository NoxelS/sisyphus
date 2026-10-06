# Laya English through LiteLLM

Laya is a CPU-only, single-replica decision service in the `litellm` namespace.
It serves the English 421M checkpoint from `convaiinnovations/laya`, pinned to
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, using Laya 0.3.28 and ONNX Runtime
1.30.0. MatMul weights use symmetric block INT8 (32 weights per block);
activations, embeddings and other operators remain floating point. Exported `Gemm`
layers are converted to MatMul before quantization, and the build requires at
least a 50% reduction in initializer storage. This is not
the upstream dynamic-activation INT8 export, which has documented decision drift.

## Client contract

For regular LiteLLM virtual keys, use `laya-english` at
`POST https://ai.noel.fyi/v1/chat/completions`. Grant the key access to this model
through the normal LiteLLM model allowlist. Send exactly one user message whose
content is the native decision request encoded as JSON. The assistant message
contains the full native decision response encoded as JSON. This is a transport
adapter: it does not generate text or interpret a conversational prompt.

Example, with the client key supplied through `LITELLM_API_KEY`:

```python
import json
import os
import urllib.request

native = {
    "state": "The sky is blue.",
    "questions": {
        "color": {
            "type": "choice",
            "instructions": "Choose the sky's color.",
            "criteria": {"blue": None, "red": None},
        }
    },
}
body = {
    "model": "laya-english",
    "messages": [{"role": "user", "content": json.dumps(native)}],
    "response_format": {"type": "json_object"},
}
request = urllib.request.Request(
    "https://ai.noel.fyi/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["LITELLM_API_KEY"],
        "Content-Type": "application/json",
        "User-Agent": "Laya-Client/1.0",
    },
)
with urllib.request.urlopen(request, timeout=70) as response:
    completion = json.load(response)
    decisions = json.loads(completion["choices"][0]["message"]["content"])
    print(decisions["answers"])
```

Cloudflare rejects Python's default user agent with error 1010; identify clients
explicitly as above. The adapter accepts only `model`, `messages`, `stream: false`,
`response_format: {"type": "json_object"}` and optional `user`. Streaming, tools,
sampling controls and conversational/multimodal messages are refused. Native
request limits still apply; the chat wrapper itself is capped at 128 KiB.
Native input/output token counts map to OpenAI prompt/completion counts; local
model token prices are zero. LiteLLM performs its standard model access checks.

The native Jev-shaped endpoint at `POST /laya/v1/systemone` remains available
for administrative experiments with the LiteLLM master key. In LiteLLM v1.104.0,
ordinary virtual keys are denied this authenticated pass-through route unless
explicitly granted `allowed_passthrough_routes`; assigning that grant requires
Enterprise. Do not distribute the master key to applications. Both transports
use the same bounded native inference handler, and the proxy authenticates to
Laya with a separate `LAYA_API_KEY` from the SOPS-encrypted `laya-runtime` Secret.
The internal Service has no separate public hostname.

Laya 0.3.28 clamps the checkpoint's temperature for 11 or more choices to its
supported minimum. Treat confidence for that bucket as uncalibrated.

Answers contain the native `choice`, `score` or `noul` result and probabilities,
with token usage. Native pass-through calls do not inherit chat-model routing or
model-specific spend enforcement. Existing Qwen chat routes keep their own configuration.

## Bounds and capacity

Only English is served. Model overrides for other checkpoints, task-routing and
non-English language overrides are rejected. Text is not translated or checked
for English automatically; callers must provide English input. Batch requests
are disabled. Request limits are 64 KiB JSON, 8,000 state characters, four
questions, 20 choices or score levels per question, and 512 tokens for either
encoder or decision-head overrides. Long inputs may be truncated by Laya; inspect
its usage/truncation fields. LiteLLM's upstream timeout is 60 seconds for both transports.

One question row executes at a time using two CPU threads. Multiple questions
in a request execute sequentially; native collation, decoding, masks and usage
stay in upstream Laya. Workspace arenas and memory-pattern caching are disabled
to avoid retaining large allocations after a long request. Two requests may be
admitted (one running, one waiting); excess requests receive 503 with
`Retry-After: 1`. The chat route also has a LiteLLM concurrency limit of two,
which may reject excess requests with 429 before they reach Laya. Two admissions are not two parallel inferences. Sustained
throughput is approximately the reciprocal of measured service time; a queued
request adds the preceding request's service time. Measure on Sisyphus before
relying on a latency or throughput SLO. CI timing describes its hosted runner.

The pod requests 250m CPU and 1152 MiB RAM, with limits of two CPUs and 2 GiB RAM.
These reservations preserve approximately one CPU and 1 GiB for LiteLLM's
migration Job under the October 6 capacity snapshot. They must be revisited if
other reservations change. CPU requests permit bursts and do not guarantee two
cores under contention. This remains a single-node service. Recreate upgrades
avoid a second resident model and cause a brief outage.

The validated AMD64 hosted-runner test used the same two-CPU/2-GiB limits and
an internal network with no egress. With four questions, 20 long choice labels
each and 512-token overrides, 10 sequential requests measured 12.95 seconds
median and 13.28 seconds maximum. A four-client burst returned two 200s
(at 12.92 and 25.83 seconds) and two immediate 503s. Service peak RSS was
1,089 MiB and final RSS 1,041 MiB; the earlier two-choice stress run peaked at
1,135 MiB. CI rejects peak RSS above the 1,152-MiB request. These are smoke-test
observations, not a production latency percentile or memory guarantee.

The initial October 6 live native-route smoke test on Sisyphus measured
0.83 seconds median and 1.01 seconds maximum for 10 short sky-color requests.
Three maximum-choice requests measured 16.32-18.29 seconds (median 17.62).
The queued request exceeded the original 30-second timeout, motivating the
60-second timeout for both transports. Peak process RSS was 1,006 MiB after
this test. These small samples do not establish a latency SLO.

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

The paired October 6 validation achieved 84.4% FP32 accuracy and 84.8% INT8
accuracy (422 and 424 correct out of 500), with 99.2% decision agreement
(496/500). Mean absolute probability drift was 0.003391; maximum drift was
0.0876. Quantization reduced initializer storage from 1,685,175,801 to
640,598,201 bytes, with 122 quantized MatMul operators.

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
the public chat route with a temporary virtual key restricted to `laya-english`.
Verify a key restricted to another model is refused, then revoke test keys. Recheck node available RAM and errors under bounded
concurrency before increasing admission or token limits.

For rollback, revert the GitOps change through Git and let Flux reconcile.
To rotate the shared upstream key, re-encrypt `laya-runtime.sops.yaml`, then
roll both Laya and LiteLLM through Git-managed pod-template annotations. There
is no persistent application state or model PVC to recover.
