ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pillow

WORKDIR /app
COPY app.py schema.sql ingest_curated.py curated-ingest-template.json ./
COPY static ./static
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]

