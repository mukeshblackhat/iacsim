# plugins/

Drop a `.py` file here to add an implementation without forking. It is imported
at startup; anything it registers becomes selectable by name in `iacsim.yaml`.

```python
# plugins/my_rule.py
from iacsim.core.interfaces import COST_RULES, CostRule

@COST_RULES.register("tls_handshake")
class TlsHandshakeRule(CostRule):
    def cost(self, edge, graph, profile):
        return {"tls": 1.5} if graph.nodes[edge.dst].kind == "lb" else {}
```

```yaml
# iacsim.yaml
latency:
  rules: [distance, processing, cold_start, tls_handshake]
```
