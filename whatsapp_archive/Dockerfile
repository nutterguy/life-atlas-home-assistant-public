ARG BUILD_ARCH
FROM devlikeapro/waha:noweb-2026.8.1@sha256:269a1ab26478763609af03c7fb30f3657a3256b2a287976cc416c61ed29c1542 AS base-amd64
FROM devlikeapro/waha:noweb-arm-2026.8.1@sha256:9385a81e3e30073a77bb15d84bc53a6c7c37e0be4909ed92e1e1ee177473fc9d AS base-aarch64
FROM base-${BUILD_ARCH}

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/nutterguy/life-atlas-connectors"

USER root
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 \
  && rm -rf /var/lib/apt/lists/*

COPY patch_waha_bind.py /tmp/patch_waha_bind.py
COPY patch_waha_noweb_sharp.py /tmp/patch_waha_noweb_sharp.py
RUN python3 /tmp/patch_waha_bind.py /app/dist/main.js \
  && python3 /tmp/patch_waha_noweb_sharp.py \
    /app/node_modules/@wppconnect-team/wppconnect/node_modules/sharp/dist/sharp.cjs \
    /app/node_modules/@wppconnect-team/wppconnect/node_modules/sharp/package.json \
  && rm /tmp/patch_waha_bind.py /tmp/patch_waha_noweb_sharp.py

COPY archive.py adapter.py secrets_init.py waha_client.py /app/life_atlas/
COPY run.sh /
RUN chmod 0755 /run.sh \
  && chmod -R a-w /app/life_atlas

CMD ["/run.sh"]
