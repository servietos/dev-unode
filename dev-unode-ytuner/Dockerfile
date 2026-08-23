FROM debian:bookworm-slim

ARG YTUNER_VERSION=1.2.6
ARG YTUNER_ARCH=x86_64-linux

RUN apt-get update && apt-get install -y --no-install-recommends \
    unzip libsqlite3-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ytuner
ADD https://github.com/coffeegreg/YTuner/releases/download/${YTUNER_VERSION}/ytuner-${YTUNER_VERSION}-${YTUNER_ARCH}.zip .
RUN unzip -o ytuner-${YTUNER_VERSION}-${YTUNER_ARCH}.zip && rm ytuner-${YTUNER_VERSION}-${YTUNER_ARCH}.zip \
    && chmod +x ytuner* 2>/dev/null || true

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80 53/udp
VOLUME /data

ENTRYPOINT ["/entrypoint.sh"]
