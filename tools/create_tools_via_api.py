"""OPTIONAL: create the 4 tools in your ElevenLabs workspace via API instead of clicking.
Needs ELEVENLABS_API_KEY in env and tools/*.json generated with real values (no YOUR-... placeholders).
Not run against the live API yet; if it errors, add tools in the dashboard instead (see tools/README.md).
"""

import json
import os
import urllib.request
from pathlib import Path


def main(tools_dir=None):
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY before creating tools via the API.")
    directory = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    paths = sorted(directory.glob("*.json"))
    unfinished = [path.name for path in paths if "YOUR-" in path.read_text()]
    if unfinished:
        raise SystemExit(
            f"{', '.join(unfinished)} still contain placeholder values; set PUBLIC_BASE_URL and "
            "ELEVENLABS_TOOL_SECRET_ID and rerun make_tools.py first (see tools/README.md)."
        )
    ids = []
    for path in paths:
        body = json.dumps({"tool_config": json.loads(path.read_text())}).encode()
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/convai/tools",
            data=body,
            method="POST",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            tool_id = json.loads(response.read()).get("id")
        ids.append(tool_id)
        print(path, "->", tool_id)
    print("Now add these tool IDs to your agent (Agent > Tools) and Publish.")
    return ids


if __name__ == "__main__":
    main()
