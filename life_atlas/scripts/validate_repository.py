import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "AGENTS.md", "CHANGELOG.md", "README.md", "app.py", "agent_api.py", "mcp_ingress_proxy.py", "google_photos_picker.py", "media_store.py", "restore_service.py", "schema.sql", "Dockerfile", "run.sh",
    "config.yaml", "repository.yaml", "importance.py", "sample-seed.json", "connectors.py", "connector_http.py", "connector_registry.py",
    "static/connector-tools.js", "docs/CONNECTORS.md", "docs/CONNECTOR_PROTOCOL_V1.md",
    "docs/ARCHITECTURE.md", "docs/DESIGN.md", "docs/DATA_MODEL.md",
    "docs/CHATGPT_INGESTION.md", "docs/GOOGLE_PHOTOS.md", "docs/SQLITE_RESTORE.md", "docs/DEPLOYMENT.md",
    "dependencies/google-photos-mcp.json", "scripts/update_google_photos_mcp.py", "scripts/promote_race_importance.py",
    "docs/WHATSAPP.md",
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
# The public mirror is reproduced from the .public-files allowlist, so a file
# that is required here but absent from that list ships an incomplete mirror
# with no other signal. Fail loudly instead of drifting silently.
allowlist_path = root / ".public-files"
if allowlist_path.exists():
    allowlisted = {
        line.strip()
        for line in allowlist_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    unlisted = [item for item in required if item not in allowlisted]
    if unlisted:
        raise SystemExit(
            "Publish allowlist drift: files required by this repository are not in "
            f".public-files, so the mirror would ship without them: {unlisted}"
        )
    unlisted_self = [
        item for item in (".public-files", "repository.yaml") if item not in allowlisted
    ]
    if unlisted_self:
        raise SystemExit(f"Publish allowlist must list itself and: {unlisted_self}")
    absent = sorted(item for item in allowlisted if not (root / item).exists())
    if absent:
        raise SystemExit(f".public-files lists files that do not exist: {absent}")
elif (root / ".git").exists():
    raise SystemExit(".public-files is missing: the publish allowlist cannot be verified")

# repository.yaml is what the Home Assistant add-on store reads to identify the
# repository. Keep its metadata pinned to config.yaml so the two copies cannot
# drift apart unnoticed.
manifest = (root / "repository.yaml").read_text(encoding="utf-8")


def _scalar(text: str, key: str) -> str | None:
    match = re.search(rf'^{key}:\s*"?([^"\n]+?)"?\s*$', text, re.MULTILINE)
    return match.group(1) if match else None


manifest_name = _scalar(manifest, "name")
manifest_url = _scalar(manifest, "url")
manifest_maintainer = _scalar(manifest, "maintainer")
if not manifest_name or not manifest_url or not manifest_maintainer:
    raise SystemExit("repository.yaml must declare name, url and maintainer")
config_name = _scalar(config, "name")
config_url = _scalar(config, "url")
if manifest_name != config_name:
    raise SystemExit(
        f"repository.yaml name {manifest_name!r} does not match config.yaml {config_name!r}"
    )
if manifest_url != config_url:
    raise SystemExit(
        f"repository.yaml url {manifest_url!r} does not match config.yaml {config_url!r}"
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

registry = (root / "connector_registry.py").read_text(encoding="utf-8")
if "REGISTRY_FILENAME" not in registry or "connectors.sqlite3" not in registry:
    raise SystemExit("The connector registry must keep its own store outside the canonical database")
_public = registry.split("def _public", 1)[1].split(chr(10) + "def ", 1)[0]
if '"auth_key":' in _public:
    raise SystemExit("A connector key must never be returned over the API")
if "connectors" in (root / "schema.sql").read_text(encoding="utf-8"):
    raise SystemExit("Connector registration must not enter the Windows-compatible canonical schema")

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

# Home Assistant decides an update exists by comparing the installed version
# string against config.yaml. It never looks at file contents. Republishing
# changed code under a version that is already installed therefore reaches
# nobody and reports no error: the mirror updates, every installation stays on
# the old build, and nothing in the pipeline says so. Seven merged fixes were
# lost that way once. These two guards make that failure loud.

# 1. The newest changelog section must be the version being shipped. This
#    catches both halves of the mistake: a version bumped with no entry, and an
#    entry filed under a version that has already gone out.
changelog_path = root / "CHANGELOG.md"
if changelog_path.exists():
    headings = [
        line[3:].strip()
        for line in changelog_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    if not headings:
        raise SystemExit("CHANGELOG.md has no version sections")
    if headings[0] != config_version.group(1):
        raise SystemExit(
            f"CHANGELOG.md's newest section is {headings[0]} but config.yaml ships "
            f"{config_version.group(1)}. Add a '## {config_version.group(1)}' section at the top; never write the "
            "entry into a section that has already been published."
        )

# 2. Shipped files must not change without a version bump. Advisory by default
#    so that concurrent branches are not forced to fight over the same version
#    number; set LIFE_ATLAS_RELEASE_GUARD=error (the publish workflow does) to
#    make it fatal at the point where it actually matters.
if (root / ".git").exists() and allowlist_path.exists():
    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    introduced = _git(
        "log", "-S", f'version: "{config_version.group(1)}"', "--format=%H", "--", "config.yaml"
    ).splitlines()
    if introduced:
        base = introduced[-1]
        changed = set(_git("diff", "--name-only", f"{base}..HEAD").splitlines())
        # CHANGELOG.md is policed above; a doc-only touch should not demand a bump.
        shipped_changed = sorted(
            (changed & allowlisted) - {"CHANGELOG.md", ".public-files"}
        )
        if shipped_changed:
            message = (
                f"Shipped files changed since {config_version.group(1)} was set, so Home Assistant "
                "would be offered no update for them: "
                f"{shipped_changed}. Run 'python scripts/deploy.py --set-version "
                "<next>' before merging to main."
            )
            if os.environ.get("LIFE_ATLAS_RELEASE_GUARD") == "error":
                raise SystemExit(message)
            print(f"warning: {message}", file=sys.stderr)

subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
print("Repository validation: ok")

