"""Build offline model assets; phases run separately to release large graphs."""

import json
import shutil
import sys
import urllib.request
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path("/opt/laya")
MODEL = ROOT / "model"
REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
SOURCE_REVISION = "a4a8921afebfd852bba0000475cfb6ab737a124c"


def main(phase):
    if phase == "download":
        snapshot_download("convaiinnovations/laya", revision=REVISION, local_dir=MODEL,
                          allow_patterns=["model.safetensors", "rl_agent_config.json",
                                          "tokenizer/*", "encoder/*"])
    elif phase == "export":
        url = f"https://raw.githubusercontent.com/nandhakishorm/laya/{SOURCE_REVISION}/scripts/export_onnx.py"
        source = urllib.request.urlopen(url, timeout=30).read()
        namespace = {"__name__": "pinned_exporter"}
        exec(compile(source, url, "exec"), namespace)
        namespace["export_to_onnx"](str(MODEL), str(ROOT / "laya.fp32.onnx"))
    elif phase == "quantize":
        from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer

        quantizer = MatMulNBitsQuantizer(str(ROOT / "laya.fp32.onnx"), bits=8,
                                       block_size=32, is_symmetric=True, accuracy_level=0,
                                       op_types_to_quantize=("MatMul",))
        # The dynamo exporter emits Gemm for many linear layers. Canonicalize those
        # first; MatMul-only quantization otherwise leaves most weights in FP32.
        quantizer.model.replace_gemm_with_matmul()
        original_bytes = sum(len(value.raw_data)
                             for value in quantizer.model.model.graph.initializer)
        quantizer.process()
        remaining_bytes = sum(len(value.raw_data)
                              for value in quantizer.model.model.graph.initializer)
        if remaining_bytes > original_bytes * .5:
            raise RuntimeError("INT8 export did not reduce initializer storage by at least 50%")
        storage = {"fp32_initializer_bytes": original_bytes,
                   "int8_initializer_bytes": remaining_bytes,
                   "quantized_matmul_count": sum(node.op_type == "MatMulNBits"
                       for node in quantizer.model.model.graph.node)}
        (ROOT / "quantization.json").write_text(json.dumps(storage, indent=2) + "\n")
        print(json.dumps(storage), flush=True)
        if not any(node.op_type == "MatMulNBits" for node in quantizer.model.model.graph.node):
            raise RuntimeError("Export did not produce any INT8 weight-only operators")
        quantizer.model.save_model_to_file(str(MODEL / "laya.int8.onnx"), True)
    elif phase == "clean":
        # Final image contains no FP32 weights, dataset, build cache or fallback model.
        (MODEL / "model.safetensors").unlink()
        for path in ROOT.glob("laya.fp32.onnx*"):
            path.unlink()
        for path in (MODEL / ".cache", ROOT / "dataset"):
            shutil.rmtree(path, ignore_errors=True)
        (ROOT / "baseline.json").unlink()
    else:
        raise ValueError(phase)


if __name__ == "__main__":
    main(sys.argv[1])
