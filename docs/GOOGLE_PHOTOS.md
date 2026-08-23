# Google Photos

Life Atlas currently has two Google Photos integration paths with different purposes.

## Picker integration

The existing Life Atlas Picker workflow cannot browse or scan the whole Google Photos library programmatically. Google shares only media the user explicitly selects for an event, diary day, or person portrait.

### Home Assistant Picker setup

1. Open Life Atlas through the final HTTPS Home Assistant address you normally use. Google does not permit an insecure LAN origin for a Web OAuth client.
2. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
3. Enable **Google Photos Picker API**. Do not select the similarly named Google Picker API.
4. Configure the OAuth consent screen and add the Google account that will use Life Atlas as a test user when the project is in testing mode.
5. Create an OAuth client with application type **Web application**.
6. Open **Photos** in Life Atlas and copy the exact origin it displays, including `https://` and any port, into **Authorised JavaScript origins** for the OAuth client.
7. Copy the Web client ID into Life Atlas and save it.
8. Read and accept the in-app disclosure, then choose **Connect Google Photos**.
9. Open an event, diary day, or person and choose **Choose from Google Photos**.

The Picker Web client ID is not a secret. Its Google access token remains only in the current browser's memory and expires automatically. It is sent to the authenticated add-on only while creating, checking, and completing one picker session.

### Picker data stored

- A compressed WebP copy up to 1600 px is stored under `/data/media`.
- The Google media item identifier, original filename, capture date, dimensions, and content hash are stored in SQLite.
- The temporary Google download URL is not stored.
- The non-secret Web client ID is stored under `/data` and is not included in backup ZIPs.

Selecting the same Google item for the same target does not create another media row. The same photo may still be deliberately attached to different targets.

## Bundled Google Photos MCP

The Home Assistant edition also bundles the upstream `savethepolarbears/google-photos-mcp` service for future ingestion workflows. It runs inside the same add-on container and is not published as a Home Assistant port.

The MCP uses a separate Google OAuth client because it needs a client secret and offline refresh token. Configure these optional Life Atlas app settings in Home Assistant:

- `google_photos_mcp_client_id`
- `google_photos_mcp_client_secret`
- `google_photos_mcp_redirect_uri`

The client secret uses Home Assistant's `password` option type. These values are read from `/data/options.json` at add-on startup and passed to the MCP process as environment variables. They are never committed to Git or baked into the container image.

### MCP token persistence

The upstream MCP normally requires its SQLite token database to be inside its project directory. Life Atlas preserves that invariant without modifying upstream code by linking `/opt/google-photos-mcp/runtime-data` to the persistent add-on directory `/data/google-photos-mcp`.

The effective token database is therefore:

```text
/data/google-photos-mcp/tokens.db
```

The directory is created with owner-only permissions and the MCP starts with `umask 077`. The upstream token database contains OAuth access and refresh tokens as plaintext JSON inside SQLite, so Home Assistant host security and backup encryption remain important. The token database must never be committed to Git or copied into the container image.

The MCP OAuth callback still needs to be routed through the authenticated Life Atlas HTTPS UI before end-user authentication is enabled. That UI/callback flow is implemented separately from storage so port 3000 can remain private.

## Disconnect and delete

For the Picker integration, choose **Disconnect and forget setup** to revoke the browser token when possible and remove the saved Web client ID. This does not delete photos already copied into Life Atlas. Use **Remove** beside a photo to delete its Life Atlas record. The underlying file is deleted when no other event, day, or person uses it.

If a Picker selection expires, is cancelled, the page reloads, or the add-on restarts, connect again and start a new selection. Life Atlas does not retain a temporary Google media URL.

Google documentation: [Picker overview](https://developers.google.com/photos/picker/guides/get-started-picker), [authorization scopes](https://developers.google.com/photos/overview/authorization), and [Photos API data policy](https://developers.google.com/photos/support/api-policy).
