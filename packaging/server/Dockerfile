# Sanger Seq Analyzer lab server (Linux). Build from the repository root:
#   docker build -f packaging/server/Dockerfile -t sanger-seq-server .
# See DEPLOY.md for running it with docker compose, HTTPS and the lab's shared folders.

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY packaging/constraints.txt ./constraints.txt
COPY src ./src
RUN pip wheel --no-cache-dir -c constraints.txt --wheel-dir /wheels ".[server]"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Dubai \
    SANGER_SEQ_DATA=/data
RUN groupadd --system --gid 10001 sangerseq \
    && useradd --system --uid 10001 --gid sangerseq --home-dir /data --no-create-home sangerseq \
    && mkdir /data && chown sangerseq:sangerseq /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels
USER sangerseq
VOLUME ["/data"]
EXPOSE 8443
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 CMD ["sanger-seq", "server", "health"]
ENTRYPOINT ["sanger-seq"]
CMD ["serve"]
