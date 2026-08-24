import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
required = [
    "AGENTS.md", "README.md", "app.py", "schema.sql", "Dockerfile", "run.sh",
    "config.yaml", "docs/ARCHITECTURE.md", "docs/DESIGN.md", "docs/DATA_MODEL.md",
    "docs/CHATGPT_INGESTION.md", "docs/DEPLOYMENT.md",
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

json.loads((root / "curated-ingest-template.json").read_text(encoding="utf-8"))
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

subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
print("Repository validation: ok")
