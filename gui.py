#!/usr/bin/env python3
"""Starts the web GUI.

Open http://localhost:8080 on this PC. To use it from an Android phone, put
the phone on the same Wi-Fi and open http://<this-pc-ip>:8080 (the address
is printed on startup), then "Add to Home screen" in Chrome.

The scrapers drive a real Chromium browser, so they run on this PC - the
phone is only a remote control. Because the GUI can start logins to your
dealer accounts, it's protected by a login: set KINETIK_GUI_USERNAME and
KINETIK_GUI_PASSWORD in .env, otherwise a random one is generated and printed on each start.

Usage:
    python gui.py
    python gui.py --port 9000
    python gui.py --local-only      # only reachable from this PC
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from kinetik_stock.config import REPO_ROOT  # noqa: E402
from kinetik_stock.gui.app import create_app  # noqa: E402


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--local-only", action="store_true", help="Only accept connections from this PC")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env", override=False)
    username = os.environ.get("KINETIK_GUI_USERNAME", "sales@kinetikcycles.com")
    password = os.environ.get("KINETIK_GUI_PASSWORD")
    generated = not password
    if generated:
        password = secrets.token_urlsafe(9)

    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    print(f"\nKinetik Stock GUI\n  This PC:  http://localhost:{args.port}")
    if not args.local_only and (ip := lan_ip()):
        print(f"  Phone:    http://{ip}:{args.port}   (same Wi-Fi)")
    print(f"  Login:    {username}\n  Password: {password}" + ("   (random - set KINETIK_GUI_PASSWORD in .env to keep one)" if generated else ""))
    print("  Ctrl+C to stop\n")

    from waitress import serve  # production server; Flask's dev server isn't meant for LAN use

    serve(create_app(username, password), host=host, port=args.port, threads=4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
