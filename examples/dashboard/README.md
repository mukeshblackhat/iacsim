# The dashboard sample

Real tool output, committed so anyone can open the page without installing
anything — and so it cannot drift: `tests/test_dashboard_sample.py` regenerates
every file here and fails when the committed copy differs (ignoring the
timestamp and the absolute paths a `diff` records).

| folder | made by | what it shows |
|---|---|---|
| `gcp-web/` | `iacsim run examples/gcp-web -o json -o html` | the map with the `page_load` path, the cold start on Cloud Run as the slowest hop |
| `foosh-load/` | `iacsim run examples/foosh-serverless --walker load -o json -o html` | a 30-node serverless stack with the users sweep (`capacity`) in the data |
| `classic-web-diff/` | `iacsim diff examples/classic-web examples/classic-web-bad -o json -o html` | RDS moved to another region: +298 ms on `page_load` |

`report.html` / `diff.html` is the file to send someone: one page, the data
inlined, opens from `file://`. `report.json` beside it is the same data as the
machine contract (schema 2). Regenerate with `make dashboard-sample` (also run
by `make examples`); `make dashboard` opens the gcp-web page.
