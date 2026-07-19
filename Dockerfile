# Lightweight, CPU-only image for data-prep (FastAPI dataset workshop).
# Torch-free: model2vec + numpy give CPU static embeddings, no GPU needed.
#
# Supply chain: the `python:3.12-slim` tag is mutable. Pin it by digest before a
# production build (kept as a tag here so the maintainer who owns the registry
# resolves the current digest deliberately):
#   docker buildx imagetools inspect python:3.12-slim   # take the index digest
#   FROM python:3.12-slim@sha256:<digest>
FROM python:3.12-slim

WORKDIR /app

# No compiler needed: every runtime dep ships prebuilt manylinux wheels.
# requirements.lock is the version-pinned (==) transitive tree the tests run
# against. (Hash-pinning via --require-hashes is a documented follow-up.)
COPY requirements.lock /app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --only-binary=:all: -r requirements.lock

COPY app/ /app/app/
COPY config.yaml /app/

# Persistent state lives under /data (mount a volume there). Defaults point the
# app at it so even a bare `docker run -v vol:/data` persists datasets and runs.
RUN mkdir -p /data/datasets /data/runs \
    && useradd -m -u 1000 appuser && chown -R appuser:appuser /app /data
USER appuser

# 1 BLAS thread: the app already caps this in-code (app/__init__ before numpy),
# these env vars are belt-and-suspenders for any library that reads them first.
# All overridable at `docker run -e VAR=...`.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    DATAPREP_DATA_DIR=/data/datasets \
    DATAPREP_RUNS_DIR=/data/runs

EXPOSE 8110
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8110/health')"

# Single-worker by design (process-local run store, LRU model cache, rate limiter).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110", "--workers", "1"]
