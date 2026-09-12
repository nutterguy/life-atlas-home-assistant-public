# Life Atlas Connector Protocol v1

## Status

Connector Protocol v1 defines the wire boundary between Life Atlas core and independently deployed connector services. The protocol is intentionally small, read-oriented, HTTP/JSON based, and source-neutral.

The protocol major version is `1`. Breaking changes require a new major version. Compatible additions may be made within v1 only when existing v1 clients can safely ignore them.

## Transport

- HTTP/1.1 or later semantics.
- JSON encoded as UTF-8.
- Connector endpoints are rooted at `/v1`.
- Clients send `Accept: application/json`.
- POST requests send `Content-Type: application/json`.
- Responses send `Content-Type: application/json` and `Cache-Control: no-store`.
- Client and server may send `X-Life-Atlas-Connector-Protocol: 1` as a diagnostic header. The authoritative compatibility value remains `protocol_version` from `/v1/info`.
- Life Atlas uses bounded requests and responses. The reference implementation currently limits request bodies to 256 KiB and JSON responses to 4 MiB.
- Timeouts are mandatory on clients. The reference client default is 5 seconds.

The protocol does not require TLS on a private Home Assistant app-to-app network. TLS is required if a connector is intentionally exposed across an untrusted network. Connector services should not be exposed to the LAN or Internet unless there is a specific operational requirement.

## Endpoints

### `GET /v1/info`

Returns connector identity and version compatibility metadata.

Required response fields:

```json
{
  "connector_id": "reference",
  "name": "Life Atlas Reference Connector",
  "protocol_version": "1.0",
  "connector_version": "0.1.0"
}
```

Optional fields:

```json
{
  "upstream_name": "provider-or-library",
  "upstream_version": "1.2.3"
}
```

`connector_id` must match the identifier configured in Life Atlas. Life Atlas fails closed when the protocol major is not supported or connector identity does not match.

### `GET /v1/status`

Returns operational status.

```json
{
  "state": "available",
  "authenticated": true,
  "last_attempted_sync": "2026-08-24T12:00:00Z",
  "last_successful_sync": "2026-08-24T12:00:00Z",
  "error": null
}
```

Allowed v1 states are:

- `available`
- `degraded`
- `unavailable`
- `auth_required`
- `incompatible`

Timestamps are ISO-8601 strings when present. They are informational in v1 and clients must not derive ordering guarantees solely from them.

### `GET /v1/capabilities`

Returns source-neutral feature names.

```json
{
  "capabilities": ["search", "media", "identities", "locations"]
}
```

Unknown capabilities must be ignored by clients. A client must not invoke an optional operation unless the relevant capability is advertised.

### `POST /v1/search`

Request:

```json
{
  "query": "harbour",
  "limit": 50,
  "cursor": "opaque-connector-cursor"
}
```

`query` is required and non-empty. `limit` is between 1 and 500. `cursor` is optional and connector-defined. Life Atlas treats cursors as opaque strings.

Response:

```json
{
  "items": [],
  "next_cursor": null
}
```

`items` is a list of SourceItem objects. `next_cursor` is either a string or `null`. Repeating a cursor is a protocol error because it can create infinite pagination loops.

## SourceItem v1

A source item is a source-neutral envelope. Fields not meaningful for a source may be `null` or omitted where the Life Atlas parser permits it.

```json
{
  "source_id": "message-001",
  "native_id": "provider-native-id",
  "item_type": "message",
  "timestamp": "2025-05-10T09:15:00Z",
  "modified_at": "2025-05-10T09:15:00Z",
  "lifecycle": "created",
  "title": null,
  "text": "Example text",
  "participants": [
    {
      "source_id": "person-alex",
      "kind": "contact",
      "label": "Alex",
      "metadata": {}
    }
  ],
  "location": null,
  "media": [],
  "content_hash": "source-content-hash",
  "metadata": {}
}
```

Required fields are `source_id` and `item_type`.

Allowed lifecycle values are:

- `created`
- `updated`
- `deleted`
- `unavailable`

Source IDs must be stable within a connector. They do not have to be globally unique because Life Atlas scopes them by connector identity.

### Participant identity

```json
{
  "source_id": "provider-person-id",
  "kind": "contact",
  "label": "Display name",
  "metadata": {}
}
```

Life Atlas does not treat a participant as a canonical person merely because a name matches. Identity resolution remains a separate Life Atlas concern.

### Media descriptor

```json
{
  "source_id": "media-id",
  "mime_type": "image/jpeg",
  "filename": "example.jpg",
  "size_bytes": 123456,
  "metadata": {
    "width": 2048,
    "height": 1365
  }
}
```

A media descriptor is metadata, not an instruction to copy the file into Life Atlas. Media retrieval and promotion are added through capabilities/endpoints in later steps without changing the existing SourceItem shape.

## Errors

Non-2xx responses use a JSON error envelope:

```json
{
  "error": {
    "code": "bad_request",
    "message": "Human-readable diagnostic"
  }
}
```

Defined reference error codes include:

- `bad_request`
- `invalid_json`
- `unsupported_media_type`
- `request_too_large`
- `not_found`
- `unavailable`
- `timeout`
- `protocol_error`
- `response_too_large`
- `internal_error`

Clients must not depend on the complete set of error code strings. HTTP status and fail-closed parsing remain authoritative.

The reference mapping is:

- `400`: malformed or invalid request
- `404`: unknown endpoint
- `413`: request exceeds allowed size
- `415`: wrong request content type
- `500`: connector/internal/protocol failure
- `503`: connector source unavailable
- `504`: connector source timeout

Life Atlas converts transport timeout and connectivity failures into isolated connector errors rather than global application failure.

## Compatibility rules

1. `/v1/info` is called before optional connector operations are trusted.
2. Connector identity must match the configured identity.
3. Protocol major must equal the Life Atlas-supported major.
4. Unknown JSON fields are ignored to permit compatible additions.
5. Missing required fields fail closed.
6. Unknown capabilities are ignored.
7. Unknown lifecycle/status enum values fail closed in v1.
8. Pagination cursors are opaque and must make forward progress.
9. Source-specific semantics belong in `metadata` or in future advertised capabilities, not in core field reinterpretation.
10. Connectors remain read-only from Life Atlas unless a future protocol explicitly introduces write capabilities.

## Security and privacy

- Connector credentials and provider tokens do not cross this API.
- Routine errors should not include source message bodies or secrets.
- The protocol has no generic remote-code, SQL, file-path, shell, or arbitrary URL operation.
- Search query text is sensitive and must not be written to routine logs by production connectors.
- Responses are `no-store` to discourage intermediary caching.
- Connector services should bind only to the interface required for app-to-app communication.
- WhatsApp and similar communication connectors must not expose provider mutation/send operations through this Life Atlas protocol.

## Reference implementation

`connector_http.py` contains the Life Atlas HTTP transport and a small protocol server adapter used by the deterministic reference connector tests. The reference server binds to loopback only and exists to prove the protocol end-to-end before Home Assistant app packaging.

The canonical behavioural model remains in `connectors.py`; the HTTP layer maps wire requests onto that model rather than introducing source-specific behaviour.
