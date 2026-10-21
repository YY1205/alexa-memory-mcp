"""Scene 7's "different assistant": a minimal local page that sends ONLY the pasted text and the
question to Amazon Nova Lite on Bedrock (no tools, no system prompt, no access to Memory
Steward), and shows the answer. Video pipeline only; not part of the product."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3

from . import config as C

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>A different assistant</title>
<style>
 body{margin:0;font:18px/1.45 -apple-system,Helvetica,Arial,sans-serif;background:#f4f1ea;color:#222}
 header{background:#3b3355;color:#fff;padding:22px 40px}
 header h1{margin:0;font-size:30px} header p{margin:6px 0 0;font-size:19px;opacity:.9}
 main{display:grid;grid-template-columns:1fr 1fr;gap:28px;padding:28px 40px}
 label{font-weight:600;display:block;margin-bottom:8px}
 textarea{width:100%;height:560px;box-sizing:border-box;font:15px/1.4 Menlo,monospace;padding:12px;border:1px solid #bbb;border-radius:8px;background:#fff}
 input{width:100%;box-sizing:border-box;font-size:20px;padding:12px;border:1px solid #bbb;border-radius:8px}
 button{margin-top:12px;font-size:19px;padding:10px 22px;border:0;border-radius:8px;background:#3b3355;color:#fff}
 #answer{margin-top:22px;min-height:120px;background:#fff;border:1px solid #ccc;border-radius:8px;padding:16px;font-size:21px;white-space:pre-wrap}
 .muted{color:#666;font-size:15px}
</style></head><body>
<header><h1>A different assistant (Amazon Nova Lite)</h1>
<p>Given only the exported file. No access to Memory Steward.</p></header>
<main>
 <div><label for="file">Pasted file</label><textarea id="file" placeholder="Paste the exported file here"></textarea></div>
 <div><label for="q">Your question</label><input id="q" autocomplete="off">
  <button id="ask">Ask</button><div id="answer" class="muted">The answer appears here.</div>
  <p class="muted">This page sends only the pasted text and your question to Nova Lite on Amazon Bedrock.</p></div>
</main>
<script>
document.getElementById("ask").onclick = async () => {
  const a = document.getElementById("answer"); a.textContent = "Thinking…"; a.className = "muted";
  const r = await fetch("/ask", {method: "POST", body: JSON.stringify({
    file: document.getElementById("file").value, question: document.getElementById("q").value})});
  const d = await r.json(); a.className = ""; a.textContent = d.answer || d.error; a.dataset.done = "1";
};
</script></body></html>"""


def ask(file_text: str, question: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=C.AWS_REGION)
    resp = client.converse(modelId=C.OTHER_MODEL_ID, messages=[{"role": "user", "content": [
        {"text": f"{file_text}\n\n{question}"}]}], inferenceConfig={"maxTokens": 300})
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            out = {"answer": ask(data["file"], data["question"])}
        except Exception as e:  # noqa: BLE001 - shown on the page, never faked
            out = {"error": f"{type(e).__name__}: {e}"}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", C.OTHER_PORT), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
