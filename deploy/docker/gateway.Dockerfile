FROM python:3.12.15-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY gateway/ ./gateway/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir ./gateway

FROM python:3.12.15-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/To6enceto/IntentLatch"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --no-create-home gateway
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "intentlatch.main:app", "--host", "0.0.0.0", "--port", "8080"]
