"""Start: python -m ycastui [web|vtuner|health]  (Standard aus YCASTUI_MODE, sonst web)."""
import os
import sys
import urllib.request


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YCASTUI_MODE", "web")
    if mode == "health":
        svc = os.environ.get("YCASTUI_MODE", "web")
        port = os.environ.get("PORT", "80" if svc == "vtuner" else "8000")
        path = "/health" if svc == "vtuner" else "/api/settings"
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=4)
        return
    if mode == "vtuner":
        from .vtuner_server import run
    else:
        from .web import run
    run()


if __name__ == "__main__":
    main()
