FROM node:22.22.0-alpine AS google_photos_mcp

RUN apk add --no-cache git python3 make g++
WORKDIR /opt/google-photos-mcp
COPY dependencies/google-photos-mcp.json /tmp/google-photos-mcp.json
RUN GOOGLE_PHOTOS_MCP_REPOSITORY="$(node -p "require('/tmp/google-photos-mcp.json').repository")" \
    && GOOGLE_PHOTOS_MCP_REF="$(node -p "require('/tmp/google-photos-mcp.json').ref")" \
    && git clone "${GOOGLE_PHOTOS_MCP_REPOSITORY}" . \
    && git checkout --detach "${GOOGLE_PHOTOS_MCP_REF}" \
    && npm ci \
    && npm run build \
    && npm prune --omit=dev \
    && rm -rf .git test coverage /tmp/google-photos-mcp.json

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
RUN apk add --no-cache python3 py3-pillow libstdc++ libgcc

WORKDIR /app
COPY app.py mcp_ingress_proxy.py google_photos_picker.py schema.sql ingest_curated.py curated-ingest-template.json ./
COPY media_store.py restore_service.py connectors.py connector_http.py connector_runtime.py runtime_entry.py ./
COPY static ./static
COPY --from=google_photos_mcp /usr/local/bin/node /usr/local/bin/node
COPY --from=google_photos_mcp /opt/google-photos-mcp /opt/google-photos-mcp
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
