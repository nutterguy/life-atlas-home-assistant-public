from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_FILE = ROOT / "dependencies" / "google-photos-mcp.json"
API_URL = "https://api.github.com/repos/savethepolarbears/google-photos-mcp/commits/"


def resolve_ref(ref: str) -> str:
    request = Request(
        API_URL + ref,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "LifeAtlasDependencyUpdater/1.0"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    sha = payload.get("sha", "")
    if len(sha) != 40:
        raise SystemExit(f"Could not resolve Google Photos MCP ref: {ref}")
    return sha


def main() -> None:
    parser = argparse.ArgumentParser(description="Update the pinned Google Photos MCP commit.")
    parser.add_argument("ref", nargs="?", default="main", help="Upstream branch, tag, or commit to resolve (default: main)")
    args = parser.parse_args()

    dependency = json.loads(DEPENDENCY_FILE.read_text(encoding="utf-8"))
    old_sha = dependency["ref"]
    new_sha = resolve_ref(args.ref)
    dependency["ref"] = new_sha
    DEPENDENCY_FILE.write_text(json.dumps(dependency, indent=2) + "\n", encoding="utf-8")

    if old_sha == new_sha:
        print(f"Google Photos MCP already pinned to {new_sha}")
    else:
        print(f"Google Photos MCP: {old_sha} -> {new_sha}")
        print("Run repository validation and build tests before committing the update.")


if __name__ == "__main__":
    main()
