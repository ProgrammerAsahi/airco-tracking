# Pinned by digest; corresponds to the python:3.12-slim multi-arch tag
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIRCO_TRACKER_HOME=/app

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml setup.py README.md ./
COPY airco_tracker ./airco_tracker
RUN pip install --no-cache-dir --no-deps .

USER appuser

ENTRYPOINT ["airco-tracker"]
CMD ["check"]
