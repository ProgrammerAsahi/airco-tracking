FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIRCO_TRACKER_HOME=/app

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml setup.py README.md ./
COPY airco_tracker ./airco_tracker
RUN pip install --no-cache-dir ".[azure]"

USER appuser

ENTRYPOINT ["airco-tracker"]
CMD ["check"]
