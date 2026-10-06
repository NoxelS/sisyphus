"""Paired held-out BoolQ accuracy gate, including probability drift and RSS."""

import json
import resource
import statistics
import sys
import time
from pathlib import Path

import torch
from datasets import load_dataset, load_from_disk
from laya.agent import Agent
from server import MODEL_DIR, MODEL_REVISION, load_agent

ROOT = Path("/opt/laya")
DATA_REVISION = "35b264d03638db9f4ce671b711558bf7ff0f80d5"
QUESTIONS = {"answer": {"type": "choice", "instructions": (
    "Read the passage and answer the question in the state. "
    "Choose yes only when the passage supports a yes answer; otherwise choose no."),
    "criteria": {"yes": None, "no": None}}}


def main(precision):
    if precision == "fp32":
        dataset = load_dataset("google/boolq", revision=DATA_REVISION, split="validation")
        dataset = dataset.shuffle(seed=42).select(range(500))
        dataset.save_to_disk(ROOT / "dataset")
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        agent = Agent(MODEL_DIR, compile=False, device="cpu")
    else:
        dataset = load_from_disk(ROOT / "dataset")
        agent = load_agent()
    records, latencies = [], []
    for row in dataset:
        start = time.perf_counter()
        result = agent.predict({"passage": row["passage"], "question": row["question"]},
                               QUESTIONS, max_len=512)
        latencies.append(time.perf_counter() - start)
        answer = result["answers"]["answer"]
        probabilities = answer["probabilities"]
        prediction = max(probabilities, key=probabilities.get)
        records.append({"prediction": prediction, "correct": prediction == (
            "yes" if row["answer"] else "no"), "probabilities": probabilities})
    accuracy = sum(record["correct"] for record in records) / len(records)
    if precision == "fp32":
        (ROOT / "baseline.json").write_text(json.dumps(records))
        print(json.dumps({"fp32_accuracy": accuracy}), flush=True)
        return
    baseline = json.loads((ROOT / "baseline.json").read_text())
    fp32_accuracy = sum(record["correct"] for record in baseline) / len(baseline)
    agreement = sum(a["prediction"] == b["prediction"] for a, b in zip(records, baseline)) / 500
    drift = [abs(a["probabilities"][key] - b["probabilities"][key])
             for a, b in zip(records, baseline) for key in a["probabilities"]]
    report = {"checkpoint_revision": MODEL_REVISION, "dataset_revision": DATA_REVISION,
              "examples": 500, "seed": 42, "max_len": 512,
              "quantization": "MatMulNBits W8A32 symmetric block_size=32 accuracy_level=0",
              "fp32_accuracy": fp32_accuracy, "int8_accuracy": accuracy,
              "decision_agreement": agreement, "mean_probability_drift": statistics.mean(drift),
              "max_probability_drift": max(drift),
              "latency_p50_seconds": statistics.median(latencies),
              "latency_p95_seconds": sorted(latencies)[474],
              "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    report.update(json.loads((ROOT / "quantization.json").read_text()))
    passed = fp32_accuracy >= .80 and accuracy >= fp32_accuracy - .02 and agreement >= .97
    report["passed"] = passed
    (ROOT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    if not passed:
        raise SystemExit("INT8 accuracy gate failed; do not publish or deploy")


if __name__ == "__main__":
    main(sys.argv[1])
