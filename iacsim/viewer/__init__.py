"""`iacsim view` — the static graph viewer.                                  [M6 ✅]

`index.html` is one self-contained page (inline CSS/JS, no CDN) that reads the
schema-2 `report.json` (or a bare `graph.json`) sitting next to it and draws
the infra graph in region/AZ swimlanes with the scenario paths overlaid. This
module only prepares the directory and serves it:

    prepare(target, cfg)  → <target>/.iacsim/ with report.json, graph.json, index.html
    bind(directory, port) → (server, url)   — the URL is known before anything blocks
    run(server, …)        → serves until Ctrl-C (or `duration` seconds); opens the browser
    serve(directory, …)   → bind + run in one call

The viewer never imports Python internals — the JSON files are its whole API,
so it works on any report.json from any version that keeps schema 2.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from iacsim.core.config import Config
from iacsim.core.interfaces import REPORTERS

VIEWER_HTML = Path(__file__).with_name("index.html")


def prepare(target: Path, cfg: Config) -> Path:
    """Make sure report.json / graph.json exist for `target`, drop index.html beside them."""
    from iacsim.core import pipeline
    base = target if target.is_dir() else target.parent
    out_dir = base / cfg.get("report.out_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (out_dir / "report.json").is_file():
        output = pipeline.run(target, cfg)
        (out_dir / "report.json").write_text(REPORTERS.get("json")().render(output.findings, output.graph))
        (out_dir / "graph.json").write_text(json.dumps(output.graph.to_dict(), indent=2, default=str))
    shutil.copyfile(VIEWER_HTML, out_dir / "index.html")
    return out_dir


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:      # no per-request noise on the CLI
        pass


def bind(directory: Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Bind the server so the URL is known up front. `port=0` picks a free one;
    a busy port raises OSError (the CLI turns it into a one-line exit 2)."""
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def run(server: ThreadingHTTPServer, open_browser: bool = True, duration: float | None = None) -> None:
    """Serve until Ctrl-C, or for `duration` seconds when given (tests)."""
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if open_browser:
        webbrowser.open(url)
    try:
        if duration is None:
            while thread.is_alive():
                time.sleep(0.5)
        else:
            time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def serve(directory: Path, port: int = 0, open_browser: bool = True, duration: float | None = None) -> str:
    """bind + run; returns the URL (kept for callers that do not need it early)."""
    server, url = bind(directory, port)
    run(server, open_browser=open_browser, duration=duration)
    return url
