"""Shared fixtures: run the real memory server as a subprocess."""
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data.seed import RECORDS  # noqa: E402
from memory.schema import to_markdown  # noqa: E402

TOKEN = "test-token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def running_server(workdir: Path):
    """python server.py on a fresh copy of the demo data; yields the base URL."""
    mem = workdir / "memories.md"
    mem.write_text(to_markdown(RECORDS))
    port = free_port()
    env = {**os.environ, "PORT": str(port), "LOCAL_MEMORY_PATH": str(mem), "MCP_BEARER_TOKEN": TOKEN,
           "DEMO_ADMIN": "1"}
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(f"{url}/healthz", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    try:
        yield url
    finally:
        proc.terminate()
        proc.wait(5)


@pytest.fixture
def http_server(tmp_path):
    with running_server(tmp_path) as url:
        yield url


@pytest.fixture(scope="module")
def module_server(tmp_path_factory):
    with running_server(tmp_path_factory.mktemp("server")) as url:
        yield url
