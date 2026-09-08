"""The dashboard — `iacsim run … -o html` and the page `iacsim view` serves.

One self-contained HTML file per run: the schema-2 report (exactly what
`report.json` holds, built by `report_payload`) is inlined into the template
`iacsim/viewer/index.html` as a JSON blob, and the page's own JavaScript draws
the map, the numbers and the findings from it. No server, no fetch, no library:
the file opens from `file://` and can be sent to someone as it is.

    render_page(payload, kind, options) → the template with the placeholder replaced by
        {"kind": "report" | "diff", "options": {top_n, all_hops}, "sections": [...],
         "report" | "diff": payload}
    _embed(obj)   → compact JSON with every `<` escaped, so a `</script>` inside an
                    evidence or warning string can never close the data block
    SECTION_TITLES → the section headings, in Brief order, so the page and the
                    terminal never disagree (a test holds them to `BriefBuilder`)

The template is read by path, not imported: `iacsim.viewer` pulls in
`http.server` and `webbrowser`, which a reporter has no business loading.
`render_diff` is deliberately minimal for now — the diff page is completed
in a later work package; it exists so `iacsim diff -o html` never tracebacks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from iacsim.core.interfaces import REPORTERS, Reporter, ReporterOptions
from iacsim.core.models import Findings, InfraGraph
from iacsim.diff.models import DiffReport
from iacsim.reporter.json_ import report_payload

TEMPLATE = Path(__file__).parent.parent / "viewer" / "index.html"
PLACEHOLDER = "__IACSIM_DATA__"

# (key, title) in the order `BriefBuilder.build()` emits them, then the capacity
# brief (`build_capacity()`): its title first, then its three sections.
SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("where", "Where the time goes"),
    ("bottlenecks", "Top bottlenecks"),
    ("recommendations", "Recommendations"),
    ("hops", "Hops"),
    ("critical_path", "Critical path"),
    ("tail_risk", "Tail risk (p99 − p50)"),
    ("warnings", "Warnings"),
    ("capacity", "users until it breaks"),
    ("utilisation", "Utilisation by users"),
    ("p99_sweep", "p99 by users"),
    ("saturation", "What breaks first"),
)


@REPORTERS.register("html")
class HtmlReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        return render_page(report_payload(findings, graph), kind="report", options=self.options)

    def render_diff(self, diff: DiffReport, before: InfraGraph, after: InfraGraph) -> str:
        payload = diff.to_dict()
        payload["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        payload["graphs"] = {"before": before.to_dict(), "after": after.to_dict()}
        return render_page(payload, kind="diff", options=self.options)


def render_page(payload: dict, kind: str = "report", options: ReporterOptions | None = None) -> str:
    """The template with its one placeholder replaced by the data blob.
    `str.replace`, never `str.format` — the template is full of CSS/JS braces."""
    if kind not in ("report", "diff"):
        raise ValueError(f"render_page: kind must be 'report' or 'diff', not {kind!r}")
    options = options or ReporterOptions()
    blob = {
        "kind": kind,
        "options": {"top_n": options.top_n, "all_hops": options.all_hops},
        "sections": [{"key": key, "title": title} for key, title in SECTION_TITLES],
        kind: payload,
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError(f"{TEMPLATE}: placeholder {PLACEHOLDER} missing")
    return template.replace(PLACEHOLDER, _embed(blob), 1)


def _embed(obj: object) -> str:
    """Compact JSON safe inside a `<script type="application/json">` block: `<`
    becomes `\\u003c`, which JSON.parse reads back as `<` but the HTML parser
    never sees as a tag — `</script>` in evidence text cannot end the block.
    Non-ASCII stays as it is: the page declares utf-8 and is written as utf-8."""
    text = json.dumps(obj, separators=(",", ":"), default=str, allow_nan=False, ensure_ascii=False)
    return text.replace("<", "\\u003c")
