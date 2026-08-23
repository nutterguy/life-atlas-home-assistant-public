import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "AGENTS.md", "README.md", "app.py", "google_photos_picker.py", "media_store.py", "schema.sql", "Dockerfile", "run.sh",
    "config.yaml", "docs/ARCHITECTURE.md", "docs/DESIGN.md", "docs/DATA_MODEL.md",
    "docs/CHATGPT_INGESTION.md", "docs/GOOGLE_PHOTOS.md", "docs/DEPLOYMENT.md",
    "dependencies/google-photos-mcp.json", "scripts/update_google_photos_mcp.py",
]
missing = [item for item in required if not (root / item).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

config = (root / "config.yaml").read_text(encoding="utf-8")
if not re.search(r'^version:\s*"\d+\.\d+\.\d+"', config, re.MULTILINE):
    raise SystemExit("config.yaml has no semantic version")

json.loads((root / "curated-ingest-template.json").read_text(encoding="utf-8"))

dependency = json.loads((root / "dependencies/google-photos-mcp.json").read_text(encoding="utf-8"))
if dependency.get("repository") != "https://github.com/savethepolarbears/google-photos-mcp.git":
    raise SystemExit("Unexpected Google Photos MCP repository")
if not re.fullmatch(r"[0-9a-f]{40}", dependency.get("ref", "")):
    raise SystemExit("Google Photos MCP dependency must be pinned to a full commit SHA")

tracked = subprocess.run(
    ["git", "ls-files"], cwd=root, check=False, capture_output=True, text=True
).stdout.splitlines()
if not tracked:
    tracked = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not any(part in {".git", "__pycache__", "data"} for part in path.parts)
    ]

private_suffixes = (
    ".db", ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
    ".pem", ".p12", ".pfx", ".key", ".zip", ".tar", ".tgz",
    ".jpg", ".jpeg", ".png", ".webp", ".heic",
)
private_names = {".env", "credentials.json", "secrets.yaml", "secrets.yml"}
text_checks = {
    "email address": re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "cloud API key": re.compile(r"\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9_-]{20,})\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "credential-bearing URL": re.compile(r"https?://[^\s/:]+:[^\s/@]+@"),
    "private network address": re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"),
    "machine-specific user path": re.compile(r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"),
}

for relative in tracked:
    path = root / relative
    lower_name = path.name.lower()
    lower_path = relative.lower()
    if lower_name in private_names or lower_path.endswith(private_suffixes):
        raise SystemExit(f"Private or generated artifact is present: {relative}")
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in text_checks.items():
        if pattern.search(content):
            raise SystemExit(f"Possible {label} in {relative}")

subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
print("Repository validation: ok")
