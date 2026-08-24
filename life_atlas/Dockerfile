FROM ghcr.io/home-assistant/base:3.21

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  org.opencontainers.image.title="Life Atlas" \
  org.opencontainers.image.description="Private life timeline for Home Assistant" \
  org.opencontainers.image.source="https://github.com/nutterguy/life-atlas-home-assistant-public" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-pillow

WORKDIR /app
COPY app.py schema.sql ingest_curated.py curated-ingest-template.json ./
COPY media_store.py connectors.py connector_http.py connector_runtime.py runtime_entry.py ./
COPY static ./static
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
