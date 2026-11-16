"""OPTIONAL: create the 4 tools in your ElevenLabs workspace via API instead of clicking.
Needs ELEVENLABS_API_KEY and ELEVENLABS_TOOL_SECRET_ID (the id of the workspace secret that
holds TOOL_SECRET) in env, and tools/*.json generated with a real PUBLIC_BASE_URL.
The secret id is filled into each config in memory and never written to disk.
Not run against the live API yet; if it errors, add tools in the dashboard instead (see tools/README.md).
"""

import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ID_PATTERN = r"[A-Za-z0-9_-]{1,128}"


def header_reference(environ):
    """The X-Tool-Secret header as a reference to the workspace secret, never its value."""
    ref = environ.get("ELEVENLABS_TOOL_SECRET_ID", "")
    if not re.fullmatch(ID_PATTERN, ref):
        raise SystemExit("Set ELEVENLABS_TOOL_SECRET_ID to the secret's id (letters, digits, - and _).")
    value = environ.get("TOOL_SECRET", "")
    if value and hmac.compare_digest(ref.encode(), value.encode()):
        raise SystemExit("ELEVENLABS_TOOL_SECRET_ID is the secret value itself; use the id ElevenLabs shows for it.")
    return {"secret_id": ref}


def main(tools_dir=None, environ=None):
    environ = os.environ if environ is None else environ
    key = environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY before creating tools via the API.")
    reference = header_reference(environ)
    directory = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    paths = sorted(directory.glob("*.json"))
    configs = {path: json.loads(path.read_text()) for path in paths}
    unfinished = [path.name for path, config in configs.items() if "YOUR-" in config["api_schema"]["url"]]
    if unfinished:
        raise SystemExit(
            f"{', '.join(unfinished)} still contain the placeholder URL; set PUBLIC_BASE_URL and "
            "rerun make_tools.py first (see tools/README.md)."
        )
    ids = []
    created = []
    for path, config in configs.items():
        config["api_schema"]["request_headers"]["X-Tool-Secret"] = reference
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/convai/tools",
            data=json.dumps({"tool_config": config}).encode(),
            method="POST",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                tool_id = json.loads(response.read()).get("id")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("detail", detail)
            except ValueError:
                pass
            print(f"HTTP {error.code} creating {path.name}: {detail}", file=sys.stderr)
            if created:
                print(
                    "Already created (delete these before retrying to avoid duplicates): "
                    + ", ".join(f"{name}={tool_id}" for name, tool_id in created),
                    file=sys.stderr,
                )
            raise SystemExit(f"ElevenLabs returned HTTP {error.code}") from None
        ids.append(tool_id)
        created.append((path.stem, tool_id))
        print(path, "->", tool_id)
    print("Now add these tool IDs to your agent (Agent > Tools) and Publish.")
    return ids


if __name__ == "__main__":
    main()
