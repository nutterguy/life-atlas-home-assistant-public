ARG BUILD_ARCH=amd64

FROM node:22.22.0-bookworm-slim AS google_photos_mcp

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ \
  && rm -rf /var/lib/apt/lists/*
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

FROM devlikeapro/waha:noweb-2026.8.1@sha256:269a1ab26478763609af03c7fb30f3657a3256b2a287976cc416c61ed29c1542 AS base-amd64
FROM devlikeapro/waha:noweb-arm-2026.8.1@sha256:9385a81e3e30073a77bb15d84bc53a6c7c37e0be4909ed92e1e1ee177473fc9d AS base-aarch64
FROM base-${BUILD_ARCH}

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  org.opencontainers.image.title="Life Atlas" \
  org.opencontainers.image.description="Private life timeline for Home Assistant" \
  org.opencontainers.image.source="https://github.com/nutterguy/life-atlas-home-assistant-public" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

USER root
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-pil \
  && rm -rf /var/lib/apt/lists/*

COPY whatsapp_archive/patch_waha_bind.py /tmp/patch_waha_bind.py
COPY whatsapp_archive/patch_waha_noweb_sharp.py /tmp/patch_waha_noweb_sharp.py
RUN python3 /tmp/patch_waha_bind.py /app/dist/main.js \
  && python3 /tmp/patch_waha_noweb_sharp.py \
    /app/node_modules/@wppconnect-team/wppconnect/node_modules/sharp/dist/sharp.cjs \
    /app/node_modules/@wppconnect-team/wppconnect/node_modules/sharp/package.json \
  && rm /tmp/patch_waha_bind.py /tmp/patch_waha_noweb_sharp.py

WORKDIR /opt/life-atlas
COPY app.py agent_api.py mcp_ingress_proxy.py google_photos_picker.py schema.sql ingest_curated.py curated-ingest-template.json sample-seed.json ./
COPY media_store.py restore_service.py connectors.py connector_http.py connector_runtime.py runtime_entry.py importance.py ./
COPY static ./static
COPY whatsapp_archive/archive.py whatsapp_archive/adapter.py whatsapp_archive/secrets_init.py whatsapp_archive/waha_client.py ./whatsapp_archive/
COPY --from=google_photos_mcp /usr/local/bin/node /usr/local/bin/node22
COPY --from=google_photos_mcp /opt/google-photos-mcp /opt/google-photos-mcp
COPY run.sh /
RUN chmod 0755 /run.sh \
  && chmod -R a-w /opt/life-atlas/whatsapp_archive

CMD ["/run.sh"]
