"""Build offline model assets; phases run separately to release large graphs."""

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
        quantizer.process()
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
