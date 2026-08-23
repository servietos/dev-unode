# Offizielles Verfahren aus coffeegreg/YTuner doc/DOCKER.md
FROM alpine:3.19

ARG YTUNER_VERSION=1.2.6
ARG YTUNER_ARCH=x86_64-linux

# libc6-compat: noetig fuer FPC-Binaries; sqlite-libs fuer den DB-Cache
RUN apk --no-cache add libc6-compat sqlite-libs unzip

WORKDIR /app
ADD https://github.com/coffeegreg/YTuner/releases/download/${YTUNER_VERSION}/ytuner-${YTUNER_VERSION}-${YTUNER_ARCH}.zip /tmp/ytuner.zip
RUN unzip -o /tmp/ytuner.zip ytuner ytuner.ini -d /app \
    && chmod +x /app/ytuner && rm /tmp/ytuner.zip

EXPOSE 80/tcp
EXPOSE 53/udp

CMD ["./ytuner"]
