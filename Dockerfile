# syntax=docker/dockerfile:1

# -- Stage 1: build dipendenze --------------------------------------
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/bootstrap_reviewed_pip.py scripts/bootstrap_reviewed_pip.py
RUN --mount=type=cache,target=/root/.cache/pip \
    python scripts/bootstrap_reviewed_pip.py --isolated-build

COPY requirements.txt .
COPY scripts/reviewed_runtime.py scripts/reviewed_runtime.py
RUN --mount=type=cache,target=/root/.cache/pip \
    python scripts/reviewed_runtime.py install --user
COPY scripts/check_dependency_audit.py scripts/check_dependency_audit.py
RUN python -m venv /tmp/pip-audit && \
    /tmp/pip-audit/bin/python scripts/bootstrap_reviewed_pip.py && \
    /tmp/pip-audit/bin/python -m pip install --no-cache-dir pip-audit==2.10.1 && \
    python scripts/reviewed_runtime.py audit --audit-python /tmp/pip-audit/bin/python \
        --report .runtime/reviewed-dependencies/runtime-audit.json

# -- Stage 2: runtime -------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Utente non-root: uid/gid fissi per compatibilita' con volume permissions
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /usr/sbin/nologin appuser

WORKDIR /app

COPY scripts/bootstrap_reviewed_pip.py scripts/bootstrap_reviewed_pip.py
RUN --mount=type=cache,target=/root/.cache/pip \
    python scripts/bootstrap_reviewed_pip.py --isolated-build

# Solo librerie runtime (niente compiler nello stage finale)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-liberation \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*


COPY --chown=appuser:appuser --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=appuser:appuser . .
COPY --from=builder /build/.runtime/reviewed-dependencies/manifest.json /app/runtime-provenance.json
COPY --from=builder /build/.runtime/reviewed-dependencies/runtime-audit.json /app/runtime-dependency-audit.json

# data/ resta scrivibile a runtime (chroma) ma non porta segreti
# nell'immagine: in docker-compose viene montata come volume esterno,
# questo mkdir e' solo fallback per run standalone.
RUN mkdir -p data/chroma /home/appuser/.local/share && \
    chown -R appuser:appuser /app /home/appuser/.local

USER appuser

RUN python scripts/reviewed_runtime.py verify --manifest /app/runtime-provenance.json \
    --audit-report /app/runtime-dependency-audit.json

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
