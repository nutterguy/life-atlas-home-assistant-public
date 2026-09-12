import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "AGENTS.md", "CHANGELOG.md", "README.md", "app.py", "agent_api.py", "mcp_ingress_proxy.py", "google_photos_picker.py", "media_store.py", "restore_service.py", "schema.sql", "Dockerfile", "run.sh",
    "config.yaml", "importance.py", "sample-seed.json", "docs/ARCHITECTURE.md", "docs/DESIGN.md", "docs/DATA_MODEL.md",
    "docs/CHATGPT_INGESTION.md", "docs/GOOGLE_PHOTOS.md", "docs/SQLITE_RESTORE.md", "docs/DEPLOYMENT.md",
    "dependencies/google-photos-mcp.json", "scripts/update_google_photos_mcp.py", "scripts/promote_race_importance.py",
    "docs/WHATSAPP.md", "whatsapp_archive/archive.py", "whatsapp_archive/adapter.py",
    "whatsapp_archive/patch_waha_bind.py", "whatsapp_archive/patch_waha_noweb_sharp.py",
    "whatsapp_archive/secrets_init.py", "whatsapp_archive/waha_client.py",
]
missing = [item for item in required if not (root / item).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

config = (root / "config.yaml").read_text(encoding="utf-8")
config_version = re.search(r'^version:\s*"(\d+\.\d+\.\d+)"', config, re.MULTILINE)
if not config_version:
    raise SystemExit("config.yaml has no semantic version")
run_script = (root / "run.sh").read_text(encoding="utf-8")
runtime_version = re.search(r'^export LIFE_ATLAS_VERSION=(\d+\.\d+\.\d+)$', run_script, re.MULTILINE)
if not runtime_version:
    raise SystemExit("run.sh has no LIFE_ATLAS_VERSION")
if runtime_version.group(1) != config_version.group(1):
    raise SystemExit(
        f"Version mismatch: config.yaml={config_version.group(1)}, "
        f"run.sh={runtime_version.group(1)}"
    )
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
if not re.search(
    rf"^##\s+(?:\[)?{re.escape(config_version.group(1))}(?:\])?(?:\s|$)",
    changelog,
    re.MULTILINE,
):
    raise SystemExit(
        f"CHANGELOG.md has no entry for current version {config_version.group(1)}"
    )
for required_option in (
    'google_photos_mcp_client_id: "str?"',
    'google_photos_mcp_client_secret: "password?"',
    'google_photos_mcp_redirect_uri: "url?"',
):
    if required_option not in config:
        raise SystemExit(f"Missing secure Google Photos MCP option schema: {required_option}")

run_script = (root / "run.sh").read_text(encoding="utf-8")
for required_fragment in (
    "MCP_DATA_DIR=/data/google-photos-mcp",
    'export TOKEN_STORAGE_PATH="runtime-data/tokens.db"',
    "chmod 700 \"$MCP_DATA_DIR\"",
    "umask 077",
    "LIFE_ATLAS_BACKEND_PORT=8100",
    "python3 /opt/life-atlas/mcp_ingress_proxy.py",
):
    if required_fragment not in run_script:
        raise SystemExit(f"Google Photos MCP persistent auth/Ingress setup missing: {required_fragment}")

proxy = (root / "mcp_ingress_proxy.py").read_text(encoding="utf-8")
for required_fragment in (
    'route == "/api/google-photos-mcp/status"',
    'route == "/api/google-photos-mcp/auth"',
    'route == "/api/google-photos-mcp/auth/callback"',
):
    if required_fragment not in proxy:
        raise SystemExit(f"Google Photos MCP Ingress bridge missing: {required_fragment}")
if '"/mcp"' in proxy:
    raise SystemExit("The raw MCP endpoint must not be exposed through Home Assistant Ingress")
if "MAX_RESTORE_CHUNK_BYTES" not in proxy:
    raise SystemExit("Ingress proxy must bound database restore chunks")
if "backup: cold" not in config:
    raise SystemExit("Life Atlas must use cold Home Assistant backups for consistent SQLite state")
restore = (root / "restore_service.py").read_text(encoding="utf-8")
for required_fragment in ("MAX_PACKAGE_EXPANDED_BYTES", "MAX_PACKAGE_RATIO", "_install_media", "content-addressed name"):
    if required_fragment not in restore:
        raise SystemExit(f"Guarded restore ZIP validation missing: {required_fragment}")

json.loads((root / "curated-ingest-template.json").read_text(encoding="utf-8"))

sample_seed = json.loads((root / "sample-seed.json").read_text(encoding="utf-8"))
for section in ("places", "sources", "people", "trips", "events", "event_people",
                "evidence", "review_items", "chapters"):
    if not isinstance(sample_seed.get(section), list):
        raise SystemExit(f"sample-seed.json is missing the {section} list")

dependency = json.loads((root / "dependencies/google-photos-mcp.json").read_text(encoding="utf-8"))
if dependency.get("repository") != "https://github.com/savethepolarbears/google-photos-mcp.git":
    raise SystemExit("Unexpected Google Photos MCP repository")
if not re.fullmatch(r"[0-9a-f]{40}", dependency.get("ref", "")):
    raise SystemExit("Google Photos MCP dependency must be pinned to a full commit SHA")

git_root = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], cwd=root, check=False,
    capture_output=True, text=True,
).stdout.strip()
if git_root and Path(git_root).resolve() == root.resolve():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
else:
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
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "cloud API key": re.compile(r"\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9_-]{20,})\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "credential-bearing URL": re.compile(r"https?://[^\s/:]+:[^\s/@]+@"),
    "private network address": re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"),
    "machine-specific user path": re.compile(r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+)"),
}
# Sample data must stay synthetic. These are SHA-256 hashes of real personal
# values that were previously embedded in the seed data, not the values
# themselves: this script ships to the public distribution repository, so
# listing them in plain text would republish exactly what it exists to keep out.
# To retire an entry, delete its hash. To add one, append
# sha256(value.lower().encode()).hexdigest().
personal_token_hashes = frozenset({
    "dc99e9aa86fab83a062cff5e0808391757071a3d5dbb942802d5f923aaead3b4",
    "8edb1ff6818b4c0eb7325264427ed9513f09b3a8dbb5a1f4d23b6e3e26e1ed2f",
    "f449e9530219ffe5df1a60474fb6e7bf20d52c72a9303de20c426d8e78e0f5f1",
    "1eeed334dffa5ca41e8a445b8b889bdf6e59710a0c261748f9a59944ebf53793",
    "3633fc73c916fc5b1c10e48246eecc5902513c614646243265e9b49ee511b719",
    "3ff80cbd2628ac69dbabad623a4f9c703d8e8f30bfd0944ef6fffbd096181e54",
    "f192804eac67673ea22d8345fdc80d5fd4892dcff7e4cc191d035efcd516f1f6",
    "ecd8275cac4acdd90fc5e6da796566bf6e111433a3f2cecea7947ceffe2bfc55",
})
word_pattern = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")

for path in tracked:
    candidate = root / path
    if candidate.name.lower() in private_names or path.lower().endswith(private_suffixes):
        raise SystemExit(f"Private or generated artifact is tracked: {path}")
    try:
        content = candidate.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in text_checks.items():
        if pattern.search(content):
            raise SystemExit(f"Possible {label} in {path}")
    leaked = {
        word
        for word in {match.lower() for match in word_pattern.findall(content)}
        if hashlib.sha256(word.encode("utf-8")).hexdigest() in personal_token_hashes
    }
    if leaked:
        # The offending values are deliberately not printed: this failure can
        # surface in logs attached to the public repository.
        raise SystemExit(
            f"Personal data must not be committed: {len(leaked)} denylisted value(s) in {path}. "
            "Sample data belongs in sample-seed.json and must be synthetic."
        )

subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "whatsapp_archive/tests", "-v"],
    cwd=root,
    check=True,
)
print("Repository validation: ok")
