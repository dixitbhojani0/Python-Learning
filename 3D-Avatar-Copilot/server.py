"""FinAdvisor AI Advisor Copilot demo server.

Serves the static demo and exchanges the Anam API key (config.json) for a
session token, so the key never reaches the browser.

Run:   python server.py
Open:  http://localhost:8123          (live avatar)
       http://localhost:8123/?dry=1   (dry run, no avatar minutes used)
"""
import json
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = 8123  # 8080 is taken by Apache on this machine
ANAM_TOKEN_URL = "https://api.anam.ai/v1/auth/session-token"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self):
        if self.path == "/api/log":  # client-side error reporting for diagnosis
            length = int(self.headers.get("Content-Length", 0))
            msg = self.rfile.read(length).decode(errors="replace")
            print("CLIENT:", msg)
            with open(ROOT / "demo.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
            self._json(200, {"ok": True})
            return
        if self.path != "/api/session-token":
            self.send_error(404)
            return

        try:
            cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            if not cfg.get("anamApiKey"):
                raise ValueError("anamApiKey is empty — paste your Anam API key into config.json")
        except Exception as e:  # missing file, bad JSON, empty key
            self._json(503, {"error": str(e)})
            return

        persona = {
            "name": cfg.get("personaName", "Penny"),
            "avatarId": cfg["avatarId"],
            "llmId": "CUSTOMER_CLIENT_V1",  # disable Anam's brain; our script drives all speech
        }
        if cfg.get("avatarModel"):
            persona["avatarModel"] = cfg["avatarModel"]
        if cfg.get("voiceId"):  # optional — avatar's default voice otherwise
            persona["voiceId"] = cfg["voiceId"]
        req = urllib.request.Request(
            ANAM_TOKEN_URL,
            data=json.dumps({"personaConfig": persona}).encode(),
            headers={
                "Authorization": f"Bearer {cfg['anamApiKey']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self._json(200, json.load(resp))
        except urllib.error.HTTPError as e:
            self._json(e.code, {"error": e.read().decode(errors="replace")})
        except Exception as e:
            self._json(502, {"error": str(e)})

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"FinAdvisor AI demo  ->  http://localhost:{PORT}")
    print(f"Dry run (no avatar minutes)  ->  http://localhost:{PORT}/?dry=1")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
