# Both stages use the same immutable Python/Debian filesystem.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements/build.lock requirements/runtime.lock /build/requirements/
RUN python -m venv /build-tools \
    && /build-tools/bin/pip install --require-hashes --only-binary=:all: -r requirements/build.lock
COPY pyproject.toml README.md MANIFEST.in requirements.txt /build/
COPY gpuroster /build/gpuroster
RUN /build-tools/bin/python -m build --wheel --no-isolation --outdir /wheels \
    && python -m venv /opt/gpuroster \
    && /opt/gpuroster/bin/pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock \
    && /opt/gpuroster/bin/pip install --no-deps /wheels/*.whl \
    && /opt/gpuroster/bin/pip check

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
LABEL org.opencontainers.image.title="GPU Roster" \
      org.opencontainers.image.version="0.6.0" \
      org.opencontainers.image.source="https://github.com/drakishev/gpuroster"
ENV PATH="/opt/gpuroster/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    GPUROSTER_HOST=0.0.0.0 GPUROSTER_PORT=18081 \
    XDG_STATE_HOME=/var/lib \
    GPUROSTER_DB_PATH=/var/lib/gpuroster/history.db \
    NVIDIA_DRIVER_CAPABILITIES=utility
RUN test "$APP_UID" -gt 0 && test "$APP_GID" -gt 0 \
    && groupadd --gid "$APP_GID" gpuroster \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --home-dir /var/lib/gpuroster --shell /usr/sbin/nologin gpuroster \
    && install -d -o "$APP_UID" -g "$APP_GID" -m 0700 /var/lib/gpuroster
COPY --from=builder /opt/gpuroster /opt/gpuroster
COPY requirements/runtime.lock /opt/gpuroster/runtime.lock
USER ${APP_UID}:${APP_GID}
WORKDIR /var/lib/gpuroster
EXPOSE 18081
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "gpuroster.healthcheck"]
ENTRYPOINT ["gpuroster"]
