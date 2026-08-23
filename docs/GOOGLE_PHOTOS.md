# Google Photos Picker

Life Atlas uses the Google Photos Picker API. It cannot browse or scan the whole Google Photos library. Google shares only the single photo selected for an event, diary day, or person portrait.

## Home Assistant setup

1. Open Life Atlas through the final HTTPS Home Assistant address you normally use. Google does not permit an insecure LAN origin for a Web OAuth client.
2. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
3. Enable **Google Photos Picker API**. Do not select the similarly named Google Picker API.
4. Configure the OAuth consent screen and add the Google account that will use Life Atlas as a test user when the project is in testing mode.
5. Create an OAuth client with application type **Web application**.
6. Open **Photos** in Life Atlas and copy the exact origin it displays, including `https://` and any port, into **Authorised JavaScript origins** for the OAuth client.
7. Copy the Web client ID into Life Atlas and save it.
8. Read and accept the in-app disclosure, then choose **Connect Google Photos**.
9. Open an event, diary day, or person and choose **Choose from Google Photos**.

The Web client ID is not a secret. The Google access token remains only in the current browser's memory and expires automatically. It is sent to the authenticated add-on only while creating, checking, and completing one picker session. No Google token or client secret is written under `/data`.

## What is stored

- A compressed WebP copy up to 1600 px is stored under `/data/media`.
- The Google media item identifier, original filename, capture date, dimensions, and content hash are stored in SQLite.
- The temporary Google download URL is not stored.
- The non-secret Web client ID is stored under `/data` and is not included in backup ZIPs.

Selecting the same Google item for the same target does not create another media row. The same photo may still be deliberately attached to different targets.

## Disconnect and delete

Choose **Disconnect and forget setup** to revoke the browser token when possible and remove the saved Web client ID. This does not delete photos already copied into Life Atlas. Use **Remove** beside a photo to delete its Life Atlas record. The underlying file is deleted when no other event, day, or person uses it.

If a selection expires, is cancelled, the page reloads, or the add-on restarts, connect again and start a new selection. Life Atlas does not retain a temporary Google media URL.

Google documentation: [Picker overview](https://developers.google.com/photos/picker/guides/get-started-picker), [authorization scopes](https://developers.google.com/photos/overview/authorization), and [Photos API data policy](https://developers.google.com/photos/support/api-policy).
