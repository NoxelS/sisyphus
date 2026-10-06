FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY runtime.txt .
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r runtime.txt
COPY server.py .

FROM runtime AS builder
RUN pip install --no-cache-dir onnx==1.23.2 onnxscript==0.7.2 datasets==5.1.0
COPY build_model.py verify_quantization.py ./
RUN python build_model.py download \
    && python verify_quantization.py fp32 \
    && python build_model.py export \
    && python build_model.py quantize \
    && python verify_quantization.py int8 \
    && python build_model.py clean

FROM runtime AS service
LABEL org.opencontainers.image.source="https://github.com/NoxelS/sisyphus" \
    org.opencontainers.image.description="Laya English 421M weight-only INT8 decision service"
# ONNX external tensor files may have owner-only permissions; avoid a chmod
# layer duplicating the weights and let the non-root runtime own the assets.
COPY --from=builder --chown=1000:1000 /opt/laya /opt/laya
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/tmp/huggingface \
    LAYA_DEVICE=cpu LAYA_THREADS=2 LAYA_MAX_CONCURRENT=2 \
    LAYA_MAX_TOKEN_BUDGET=512 LAYA_IDLE_UNLOAD_SECONDS=0
COPY bounded_server.py chat_adapter.py ./
USER 1000:1000
EXPOSE 8000
CMD ["python", "bounded_server.py"]
