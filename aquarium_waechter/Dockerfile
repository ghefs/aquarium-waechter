ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM
RUN apk add --no-cache python3
COPY waechter /app/waechter
COPY run.sh /app/
RUN chmod a+x /app/run.sh
WORKDIR /app
CMD ["/app/run.sh"]
