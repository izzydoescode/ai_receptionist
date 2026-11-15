"""OPTIONAL: create the 4 tools in your ElevenLabs workspace via API instead of clicking.
Needs ELEVENLABS_API_KEY in env. Not run by the kit author (no key); if it errors, add tools in the dashboard
by pasting tools/*.json fields (Agent > Tools > Add tool > Webhook).
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
    ids = []
    for path in sorted(directory.glob("*.json")):
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
