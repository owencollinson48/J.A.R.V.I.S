"""Run JARVIS on this computer. No cloud deployment is needed."""
import argparse
import threading
import webbrowser

import uvicorn

from jarvis.app import create_app


def main():
    parser = argparse.ArgumentParser(description="JARVIS personal assistant")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
