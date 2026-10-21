"""Run the real local demo (memory server + Simulated Alexa+) the way scripts/run_demo.sh does,
with MEMORY_BACKEND=drive and Nova Pro. The owner passphrase is generated here and only kept
in memory: it is typed into the masked sign-in field and never printed or logged."""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from . import config as C

sys.path.insert(0, str(C.ROOT))
from auth.passphrase import hash_passphrase  # noqa: E402  (the product's own helper, unchanged)


class Demo:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.passphrase = "-".join(secrets.token_hex(3) for _ in range(4))
        self.procs: list[subprocess.Popen] = []
        self.tmp = tempfile.TemporaryDirectory(prefix="memory-steward-video-")
        self.sim_url = f"http://127.0.0.1:{C.SIM_PORT}"
        self.server_url = f"http://127.0.0.1:{C.SERVER_PORT}"

    def __enter__(self) -> "Demo":
        folder = os.environ.get("DRIVE_FOLDER_ID")
        key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS",
                             str(Path.home() / ".config/alexa-memory-mcp/service-account.json"))
        if not folder:
            raise SystemExit("Set DRIVE_FOLDER_ID (the demo Drive folder) for MEMORY_BACKEND=drive.")
        work = Path(self.tmp.name)
        base = {k: v for k, v in os.environ.items() if k not in ("OWNER_PASSPHRASE", "MCP_BEARER_TOKEN")}
        client_secret = secrets.token_urlsafe(32)
        oauth = {"OAUTH_CLIENT_ID": "simulated-alexa", "OAUTH_CLIENT_SECRET": client_secret,
                 "OAUTH_REDIRECT_URIS": f"{self.sim_url}/oauth/callback"}
        server_env = {**base, **oauth, "MEMORY_BACKEND": "drive", "DRIVE_FOLDER_ID": folder,
                      "GOOGLE_APPLICATION_CREDENTIALS": key, "MEMORY_REFRESH_SECONDS": "2",
                      "DEMO_ADMIN": "1", "HOST": "127.0.0.1", "PORT": str(C.SERVER_PORT),
                      "OWNER_PASSPHRASE_HASH": hash_passphrase(self.passphrase),
                      "OAUTH_TOKEN_STORE": str(work / "tokens.json")}
        sim_env = {**base, **oauth, "MCP_SERVER_URL": f"{self.server_url}/mcp", "SIM_HOST": "127.0.0.1",
                   "SIM_PORT": str(C.SIM_PORT), "SIM_PUBLIC_URL": self.sim_url, "SIM_BRAIN": "bedrock",
                   "BEDROCK_MODEL_ID": C.MODEL_ID, "AWS_REGION": C.AWS_REGION}
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._start([sys.executable, "server.py"], server_env, "server.log", f"{self.server_url}/healthz")
        self._start([sys.executable, "-m", "simulator.app"], sim_env, "simulator.log", f"{self.sim_url}/healthz")
        return self

    def _start(self, cmd, env, log_name, health):
        log = open(self.log_dir / log_name, "w")
        proc = subprocess.Popen(cmd, cwd=C.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        for _ in range(300):  # Drive startup downloads the file first
            if proc.poll() is not None:
                raise SystemExit(f"{cmd[-1]} exited; see {self.log_dir / log_name}")
            try:
                if httpx.get(health, timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise SystemExit(f"{cmd[-1]} did not become healthy")

    def memory_markdown(self) -> str:
        r = httpx.get(f"{self.sim_url}/api/memory", timeout=20)
        r.raise_for_status()
        return r.json()["markdown"]

    def __exit__(self, *exc):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.tmp.cleanup()
