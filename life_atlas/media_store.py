from __future__ import annotations

import base64
import hashlib
import io
from contextlib import closing
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps


MAX_UPLOAD_BYTES = 40 * 1024 * 1024


def decode_data_url(value: str) -> bytes:
    if not value.startswith("data:image/") or "," not in value:
        raise ValueError("Choose an image file")
    raw = base64.b64decode(value.split(",", 1)[1], validate=True)
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("The image must be smaller than 40 MB")
    return raw


def normalise_image(raw: bytes, max_dimension: int = 1600) -> tuple[bytes, int, int]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            if image.mode == "RGBA":
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "WEBP", quality=82, method=4)
            return output.getvalue(), image.width, image.height
    except Exception as exc:
        raise ValueError("Life Atlas could not read that image") from exc


def _target(payload: dict) -> tuple[int | None, int | None, str]:
    event_id = int(payload["event_id"]) if payload.get("event_id") else None
    person_id = int(payload["person_id"]) if payload.get("person_id") else None
    captured_date = str(payload.get("captured_date") or "")[:10]
    if captured_date:
        try:
            date.fromisoformat(captured_date)
        except ValueError:
            raise ValueError("Photo date must use YYYY-MM-DD") from None
    if event_id and person_id:
        raise ValueError("A photo cannot be an event and person portrait at the same time")
    if not (event_id or person_id or captured_date):
        raise ValueError("Choose an event, person or day for the photo")
    return event_id, person_id, captured_date


def store_image(connect, data_dir: Path, raw: bytes, payload: dict, *, max_dimension: int = 1600) -> dict:
    event_id, person_id, captured_date = _target(payload)
    normalised, width, height = normalise_image(raw, max_dimension=max_dimension)
    digest = hashlib.sha256(normalised).hexdigest()
    relative = Path("media") / digest[:2] / f"{digest}.webp"
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(normalised)
    featured = 1 if payload.get("is_featured", True) else 0
    with closing(connect()) as con, con:
        if event_id and not con.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
            raise ValueError("Event not found")
        if person_id and not con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone():
            raise ValueError("Person not found")
        if featured:
            if event_id:
                con.execute("UPDATE media SET is_featured=0 WHERE event_id=?", (event_id,))
            elif person_id:
                con.execute("UPDATE media SET is_featured=0 WHERE person_id=?", (person_id,))
            else:
                con.execute("UPDATE media SET is_featured=0 WHERE event_id IS NULL AND person_id IS NULL AND captured_date=?", (captured_date,))
        cur = con.execute(
            """INSERT INTO media(event_id,person_id,captured_date,media_type,local_path,external_url,
               caption,is_featured,source_id,source_ref,mime_type,sha256,width,height,created_at)
               VALUES(?,?,?,'photo',?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (event_id, person_id, captured_date, relative.as_posix(), payload.get("external_url", ""),
             str(payload.get("caption") or "")[:500], featured, payload.get("source_id"),
             str(payload.get("source_ref") or "")[:1000], "image/webp", digest, width, height),
        )
        media_id = cur.lastrowid
    return {"id": media_id, "url": f"api/media/{media_id}", "width": width, "height": height, "sha256": digest}


def clone_media(connect, media_id: int, payload: dict) -> int:
    event_id, person_id, captured_date = _target(payload)
    with closing(connect()) as con, con:
        source = con.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if not source:
            raise ValueError("Photo not found")
        featured = 1 if payload.get("is_featured", True) else 0
        if featured and event_id:
            con.execute("UPDATE media SET is_featured=0 WHERE event_id=?", (event_id,))
        cur = con.execute(
            """INSERT INTO media(event_id,person_id,captured_date,media_type,local_path,external_url,
               caption,is_featured,source_id,source_ref,mime_type,sha256,width,height,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (event_id, person_id, captured_date, source["media_type"], source["local_path"], source["external_url"],
             payload.get("caption", source["caption"]), featured, source["source_id"], source["source_ref"],
             source["mime_type"], source["sha256"], source["width"], source["height"]),
        )
        return cur.lastrowid


def media_file(connect, data_dir: Path, media_id: int) -> tuple[Path, str]:
    con = connect()
    try:
        row = con.execute("SELECT local_path,mime_type FROM media WHERE id=?", (media_id,)).fetchone()
    finally:
        con.close()
    if not row or not row["local_path"]:
        raise ValueError("Photo not found")
    root = data_dir.resolve()
    path = (root / row["local_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Photo file not found")
    return path, row["mime_type"] or "application/octet-stream"
