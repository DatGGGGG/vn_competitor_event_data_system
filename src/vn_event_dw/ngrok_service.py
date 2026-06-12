from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Callable

from pyngrok import conf as ngrok_conf
from pyngrok import ngrok
import uvicorn

from .api import create_app


def _wait_for_port(host: str, port: int, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for API server on {host}:{port}: {last_error}")


def serve_api_with_ngrok(
    *,
    db_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    ngrok_authtoken: str | None = None,
    ngrok_domain: str | None = None,
    progress: Callable[[str], None] = print,
) -> None:
    app = create_app(db_path=db_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    thread = threading.Thread(target=server.run, name="vn-event-dw-api", daemon=True)
    thread.start()
    _wait_for_port(host, port)

    authtoken = (ngrok_authtoken or os.getenv("NGROK_AUTHTOKEN", "")).strip()
    domain = (ngrok_domain or os.getenv("NGROK_DOMAIN", "")).strip()
    ngrok_root = db_path.resolve().parent / ".ngrok"
    ngrok_root.mkdir(parents=True, exist_ok=True)
    ngrok_binary = ngrok_root / ("ngrok.exe" if os.name == "nt" else "ngrok")
    pyngrok_config = ngrok_conf.PyngrokConfig(
        auth_token=authtoken or None,
        ngrok_path=str(ngrok_binary),
        config_path=str(ngrok_root / "ngrok.yml"),
        ngrok_version="3",
        config_version="3",
    )
    connect_options: dict[str, str] = {}
    if domain:
        connect_options["url"] = domain
    tunnel = ngrok.connect(addr=str(port), proto="http", pyngrok_config=pyngrok_config, **connect_options)
    progress(f"ngrok_tunnel_url: {tunnel.public_url}")
    progress(f"local_api_url: http://{host}:{port}")

    try:
        while thread.is_alive() and not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        progress("ngrok_tunnel_stopping")
    finally:
        server.should_exit = True
        try:
            ngrok.disconnect(tunnel.public_url, pyngrok_config=pyngrok_config)
        finally:
            ngrok.kill(pyngrok_config=pyngrok_config)
        thread.join(timeout=5)
