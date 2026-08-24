# Google Photos

Life Atlas has two Google Photos integrations with different purposes.

## Persistent Google Photos MCP connection

The bundled Google Photos MCP is intended for broader Life Atlas photo workflows and future ingestion. Its OAuth refresh credentials persist locally in the Home Assistant app data directory.

### Home Assistant setup

1. Open **Settings > Apps > Life Atlas > Configuration**.
2. Enter the Google OAuth Web client ID in `google_photos_mcp_client_id`.
3. Enter its client secret in `google_photos_mcp_client_secret`.
4. Open Life Atlas through the final HTTPS Home Assistant address you normally use and choose **Photos**.
5. Life Atlas displays the exact MCP callback URL for this Ingress session. Copy that complete URL into the OAuth client's **Authorised redirect URIs** in Google Cloud.
6. Enter the same complete URL in the Life Atlas app option `google_photos_mcp_redirect_uri`, save the app configuration, and restart Life Atlas.
7. Return to **Photos**. The MCP card should report **Available**, **Configured**, and show that the configured redirect matches.
8. Choose **Connect MCP**, complete Google consent in the pop-up, then return to Life Atlas. The status card refreshes automatically when credentials have been stored.

Home Assistant publishes only the Life Atlas Ingress port. The MCP service itself remains internal to the add-on on port 3000. Life Atlas proxies only the OAuth start/callback and status paths needed for setup.

### What the MCP stores

- OAuth refresh/access token state is stored under `/data/google-photos-mcp/tokens.db`.
- The directory is owner-only and the MCP starts with `umask 077`.
- The upstream MCP stores its token JSON in SQLite without application-level encryption. Protect Home Assistant host access and backups accordingly.
- OAuth client configuration is kept in Home Assistant app options, not in Git or the Docker image.
- The MCP client secret and token strings are never returned by the Life Atlas status API.

The MCP token database is deliberately separate from `life_atlas.sqlite3` and from the existing Picker browser token.

## Google Photos Picker

The Picker workflow is for deliberate attachment of individual photos to an event, diary day, or person. It cannot silently scan the whole Google Photos library. Google shares only items explicitly selected in its Picker UI.

### Picker setup

1. Open Life Atlas through the final HTTPS Home Assistant address you normally use. Google does not permit an insecure LAN origin for a Web OAuth client.
2. Create or select a project in Google Cloud Console.
3. Enable **Google Photos Picker API**. Do not select the similarly named Google Picker API.
4. Configure the OAuth consent screen and add the Google account that will use Life Atlas as a test user when the project is in testing mode.
5. Create an OAuth client with application type **Web application**.
6. Open **Photos** in Life Atlas and copy the exact origin it displays, including `https://` and any port, into **Authorised JavaScript origins** for the OAuth client.
7. Copy the Web client ID into Life Atlas and save it.
8. Read and accept the in-app disclosure, then choose **Connect Picker**.
9. Open an event, diary day, or person and choose **Choose from Google Photos**.

The Picker Web client ID is not a secret. Its Google access token remains only in the current browser's memory and expires automatically. It is sent to the authenticated add-on only while creating, checking, and completing one Picker session. No Picker access token or client secret is written under `/data`.

### Picker media storage

- A compressed WebP copy up to 1600 px is stored under `/data/media`.
- The Google media item identifier, original filename, capture date, dimensions, and content hash are stored in SQLite.
- The temporary Google download URL is not stored.
- The non-secret Picker Web client ID is stored under `/data` and is not included in backup ZIPs.

Selecting the same Google item for the same target does not create another media row. The same photo may still be deliberately attached to different targets.

Choose **Disconnect and forget Picker setup** to revoke the browser token when possible and remove the saved Picker Web client ID. This does not delete photos already copied into Life Atlas. Use **Remove** beside a photo to delete its Life Atlas record. The underlying file is deleted when no other event, day, or person uses it.

Google documentation: Picker overview, Photos authorization scopes, and Photos API data policy are linked from Google's developer documentation.
