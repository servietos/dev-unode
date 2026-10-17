#!/usr/bin/env python3
"""Small, read-only-by-default Goldcoin JSON-RPC web console."""
import base64
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8332")
AUTH = "Basic " + base64.b64encode(
    f"{os.environ['RPC_USER']}:{os.environ['RPC_PASSWORD']}".encode()
).decode()
PORT = int(os.environ.get("PORT", "3000"))

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Goldcoin Core</title>
<style>body{font:16px system-ui;max-width:860px;margin:3rem auto;padding:0 1rem;background:#10151d;color:#e8edf2}h1{color:#f5c542}input,button{font:inherit;padding:.7rem;margin:.3rem;background:#202a36;color:inherit;border:1px solid #485668;border-radius:5px}button{cursor:pointer}pre{white-space:pre-wrap;background:#1a222d;padding:1rem;border-radius:6px;min-height:8rem}small{color:#9eacba}</style>
<h1>Goldcoin Core</h1><p>Node status and JSON-RPC console</p><button onclick="rpc('getblockchaininfo')">Blockchain</button><button onclick="rpc('getnetworkinfo')">Network</button><button onclick="rpc('getpeerinfo')">Peers</button><form onsubmit="event.preventDefault();rpc(document.querySelector('#method').value)"><input id="method" value="getblockchaininfo" aria-label="RPC method"><button>Run RPC</button></form><small>RPC-Methoden mit Seiteneffekten wie sendtoaddress nur bewusst aufrufen.</small><pre id="out">Loading status…</pre>
<script>async function rpc(method){let o=document.querySelector('#out');o.textContent='Loading…';try{let r=await fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({method})});let d=await r.json();o.textContent=JSON.stringify(d,null,2)}catch(e){o.textContent=String(e)}}rpc('getblockchaininfo')</script>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/rpc":
            self.send_error(404)
            return
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            method = request.get("method", "")
            if not isinstance(method, str) or not method.replace("_", "").isalnum():
                raise ValueError("Invalid RPC method")
            payload = json.dumps({"jsonrpc": "1.0", "id": "web", "method": method, "params": request.get("params", [])}).encode()
            req = urllib.request.Request(RPC_URL, data=payload, headers={"Authorization": AUTH, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())
            status = 200
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except Exception:
                result = {"error": str(exc)}
            status = 502
        except Exception as exc:
            result = {"error": str(exc)}
            status = 502
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print("web:", format % args, flush=True)

ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
