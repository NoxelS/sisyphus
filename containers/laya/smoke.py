"""Exercise the built, offline HTTP service; called inside the running container."""

import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

URL = "http://127.0.0.1:8000"


def request(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, headers=headers),
                                    timeout=90) as response:
            result = json.load(response)
            print(f"HTTP {path}: {response.status}", flush=True)
            return response.status, result
    except urllib.error.HTTPError as error:
        return error.code, None


def main():
    for _ in range(120):
        try:
            if request("/health")[0] == 200:
                break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise SystemExit("Service never became ready")
    body = {"state": "The sky is blue.", "questions": {"color": {
        "type": "choice", "instructions": "Choose the sky's color.",
        "criteria": {"blue": None, "red": None}}}}
    assert request("/v1/systemone", body)[0] == 401
    assert request("/v1/systemone", body, "invalid")[0] == 401
    status, result = request("/v1/systemone", body, "smoke-key")
    assert status == 200, status
    assert result["answers"]["color"]["choice"] == "blue", result
    assert result["routing"]["model"] == "english", result
    for overrides in ({"model": "multilingual"}, {"task": "typed_decisions"},
                      {"lang": "de"}, {"max_len": 513}):
        assert request("/v1/systemone", body | overrides, "smoke-key")[0] == 422
    assert request("/v1/systemone/batch", body, "smoke-key")[0] == 404
    assert request("/v1/systemone", body | {"state": "x" * 8001}, "smoke-key")[0] in (413, 422)
    status, result = request("/v1/systemone", {"state": "The sky is blue.", "questions": {
        "truth": {"type": "noul", "instructions": "Is the sky blue?",
                  "criteria": {"false": "No", "true": "Yes"}},
        "strength": {"type": "score", "instructions": "Rate evidence that the sky is blue.",
                     "criteria": ["none", "weak", "strong"]}}}, "smoke-key")
    assert status == 200, status
    assert result["answers"]["truth"]["type"] == "noul", result
    assert result["answers"]["strength"]["type"] == "score", result
    mixed = {"color": body["questions"]["color"],
             "truth": {"type": "noul", "instructions": "Is the sky blue?",
                       "criteria": {"false": "No", "true": "Yes"}},
             "strength": {"type": "score", "instructions": "Rate evidence that the sky is blue.",
                          "criteria": ["none", "weak", "strong"]}}
    status, combined = request("/v1/systemone", {"state": body["state"], "questions": mixed},
                               "smoke-key")
    assert status == 200
    for name, question in mixed.items():
        status, single = request("/v1/systemone", {"state": body["state"],
                                "questions": {name: question}}, "smoke-key")
        assert status == 200
        answer = combined["answers"][name]
        reference = single["answers"][name]
        if "probabilities" in answer:
            assert all(abs(value - reference["probabilities"][key]) <= .002
                       for key, value in answer["probabilities"].items()), (answer, reference)
        else:
            assert abs(answer["noul"] - reference["noul"]) <= .002, (answer, reference)
    stress = {"state": "The sky is blue. " * 100,
              "questions": {f"color{i}": body["questions"]["color"] | {
                  "criteria": {f"color {option:02}: " + "a detailed alternative " * 4: None
                               for option in range(20)}} for i in range(4)},
              "max_len": 512, "head_max_len": 512}
    print("Starting maximum-size inference", flush=True)
    assert request("/v1/systemone", stress, "smoke-key")[0] == 200
    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        assert request("/v1/systemone", stress, "smoke-key")[0] == 200
        latencies.append(time.perf_counter() - start)
    barrier = threading.Barrier(4)

    def concurrent_request(_):
        barrier.wait()
        start = time.perf_counter()
        status, _ = request("/v1/systemone", stress, "smoke-key")
        return {"status": status, "seconds": time.perf_counter() - start}

    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrency = list(pool.map(concurrent_request, range(4)))
    assert all(row["status"] in (200, 503) for row in concurrency), concurrency
    assert sorted(row["status"] for row in concurrency) == [200, 200, 503, 503], concurrency
    status = Path("/proc/1/status").read_text().splitlines()
    memory = {line.split(":")[0]: int(line.split()[1]) / 1024 for line in status
              if line.startswith(("VmRSS:", "VmHWM:"))}
    report = {"max_request_latency_p50_seconds": statistics.median(latencies),
              "max_request_latency_p95_seconds": max(latencies),
              "four_client_burst": concurrency, "service_memory_mib": memory}
    Path("/tmp/runtime-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    assert memory["VmHWM"] <= 1152, "Peak RSS exceeds the Kubernetes reservation"
    print("English HTTP, authentication, routing and request-bound checks passed")


if __name__ == "__main__":
    main()
