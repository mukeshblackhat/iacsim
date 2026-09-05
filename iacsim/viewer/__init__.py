"""`iacsim view` — the static graph viewer.                                  [M6 ✅]

`index.html` is one self-contained page (inline CSS/JS, no CDN) that reads the
schema-2 `report.json` (or a bare `graph.json`) sitting next to it and draws
the infra graph in region/AZ swimlanes with the scenario paths overlaid. This
module only prepares the directory and serves it:

    prepare(target, cfg)  → <target>/.iacsim/ with report.json, graph.json, index.html
                            (the pipeline runs when report.json is missing or older than
                            the IaC / scenarios / config files next to the target)
    bind(directory, port) → (server, url)   — the URL is known before anything blocks
    run(server, …)        → serves until Ctrl-C (or `duration` seconds); opens the browser
    serve(directory, …)   → bind + run in one call

The viewer never imports Python internals — the JSON files are its whole API,
so it works on any report.json from any version that keeps schema 2.
"""

from __future__ import annotations

import shutil
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from iacsim.core.config import Config
from iacsim.reporter.writer import write_graph, write_reports

VIEWER_HTML = Path(__file__).with_name("index.html")
INPUT_GLOBS = ("*.tf", "*.tf.json", "*.tofu", "*.tfvars", "template.json", "template.y*ml",
               "scenarios.yaml", "iacsim.yaml", "load.yaml")


def prepare(target: Path, cfg: Config) -> Path:
    """Make sure a fresh report.json / graph.json exist for `target`, drop index.html beside them."""
    from iacsim.core import pipeline
    base = target if target.is_dir() else target.parent
    out_dir = base / cfg.get("report.out_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    if is_stale(out_dir / "report.json", base):
        output = pipeline.run(target, cfg)
        write_reports(["json"], lambda r: r.render(output.findings, output.graph), "report", out_dir)
        write_graph(output.graph, out_dir)
    shutil.copyfile(VIEWER_HTML, out_dir / "index.html")
    return out_dir


def is_stale(report: Path, base: Path) -> bool:
    """No report yet, or any input file beside the target is newer than it."""
    if not report.is_file():
        return True
    newest = max((p.stat().st_mtime_ns for g in INPUT_GLOBS for p in base.glob(g)), default=0)
    return newest > report.stat().st_mtime_ns


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
