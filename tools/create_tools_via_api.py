"""OPTIONAL: create the 4 tools in your ElevenLabs workspace via API instead of clicking.
Needs ELEVENLABS_API_KEY in env. Not run by the kit author (no key); if it errors, add tools in the dashboard
by pasting tools/*.json fields (Agent > Tools > Add tool > Webhook).
"""
import json, os, glob, urllib.request
key = os.environ["ELEVENLABS_API_KEY"]
for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "*.json"))):
    body = json.dumps({"tool_config": json.load(open(f))}).encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/convai/tools", data=body, method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        print(f, "->", json.loads(r.read()).get("id"))
print("Now add these tool IDs to your agent (Agent > Tools) and Publish.")
