#!/usr/bin/env python3
"""
Desktop & Web Launcher for News Archiver.
Starts the FastAPI server and opens a native desktop window via pywebview
(using Edge WebView2 on Windows), or runs in browser/server mode with --web / --server-only.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def setup_logging_and_streams() -> None:
    """
    In Windows PyInstaller windowed builds (--noconsole), sys.stdout and sys.stderr are None.
    Redirect None streams to a log file (or os.devnull) so uvicorn/logging never crashes.
    """
    log_dir = Path.home() / "Documents" / "NewsArchiver"
    log_file_path: Path | None = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / "news_archiver.log"
    except OSError:
        log_file_path = None

    if sys.stdout is None or sys.stderr is None:
        target = open(log_file_path, "a", encoding="utf-8") if log_file_path else open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = target
        if sys.stderr is None:
            sys.stderr = target

    handlers: list[logging.Handler] = []
    if log_file_path:
        try:
            handlers.append(logging.FileHandler(log_file_path, encoding="utf-8"))
        except OSError:
            pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers or None,
    )


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def wait_for_server(url: str, timeout_sec: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> None:
    setup_logging_and_streams()

    parser = argparse.ArgumentParser(description="News Archiver Desktop & Web App")
    parser.add_argument("--web", action="store_true", help="Open in the default web browser instead of a native window")
    parser.add_argument("--server-only", action="store_true", help="Run as a headless web server without opening a window")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0 = auto-select free port)")
    args = parser.parse_args()

    # Import after stream setup so frozen Windows builds never fail on None stdout/stderr
    import uvicorn
    from app_server import app

    port = args.port if args.port > 0 else (8000 if args.server_only else find_free_port(args.host))
    base_url = f"http://{args.host}:{port}"

    if args.server_only:
        uvicorn.run(app, host=args.host, port=port, log_config=None, loop="asyncio")
        return

    config = uvicorn.Config(app, host=args.host, port=port, log_config=None, loop="asyncio")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not wait_for_server(f"{base_url}/api/health", timeout_sec=15.0):
        logging.error("Backend server did not start within 15 seconds.")
        sys.exit(1)

    if args.web:
        webbrowser.open(base_url)
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return

    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.create_window(
            title="News Archiver — Reader Mode PDF Generator",
            url=base_url,
            width=1180,
            height=840,
            min_size=(820, 600),
            text_select=True,
        )
        webview.start()
    except Exception as exc:
        logging.warning("pywebview unavailable (%s); falling back to default browser.", exc)
        webbrowser.open(base_url)
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
