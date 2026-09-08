"""Inference rule: gcp_pubsub_push                                          [M11]

    google_pubsub_subscription.topic                       → topic —PUBLISH→ subscription
    google_pubsub_subscription.push_config.push_endpoint   → subscription —CONSUME→ the Cloud Run /
                                                             Cloud Functions node whose URL it is
    google_pubsub_subscription.bigquery_config.table       → subscription —WRITE→ the BigQuery table
    google_pubsub_subscription.cloud_storage_config.bucket → subscription —WRITE→ the bucket

A placeholder endpoint (`${google_cloud_run_v2_service.x.uri}/push`) is HIGH; a
literal `https://<name>-<hash>-<region>.a.run.app/…` is matched on the service
name prefix and is LOW, and the evidence says so. Delivery is charged once: the
delay lives on `pubsub_subscription.consume`, so the topic → subscription hop is
a PUBLISH and the consumer hop leaves *from the subscription* as a CONSUME —
never topic —CONSUME→ subscription, never topic → consumer directly.

Deliberately left: a pull subscription has no consumer here — that edge comes
from the subscriber's IAM grant (`gcp_iam_binding`); `dead_letter_policy.dead_letter_topic`
is a failure path, not a request hop.
"""

from __future__ import annotations

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResources
from iacsim.graph.inference._common import block, first_node, node_for_url, raws_of_type, short

SINKS = (("bigquery_config", "table", ("bigquery",)), ("cloud_storage_config", "bucket", ("gcs",)))


@INFERENCE_RULES.register("gcp_pubsub_push")
class GcpPubsubPushRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for sub in raws_of_type(raw, "google_pubsub_subscription"):
            if sub.address not in graph.nodes:
                continue
            topic = first_node(graph, sub.attrs.get("topic"), [NodeKind.QUEUE])
            if topic and topic != sub.address:
                edges.append(Edge(topic, sub.address, EdgeKind.PUBLISH, Confidence.HIGH,
                                  f"{short(sub.address)} subscribes to {short(topic)}"))
            endpoint = block(sub.attrs.get("push_config")).get("push_endpoint")
            if (hit := node_for_url(graph, endpoint)) is not None:
                target, confidence, how = hit
                edges.append(Edge(sub.address, target, EdgeKind.CONSUME, confidence,
                                  f"{short(sub.address)} push_config.push_endpoint is {how} for {short(target)}"))
            for config, attr, subtypes in SINKS:
                sink = first_node(graph, block(sub.attrs.get(config)).get(attr), [NodeKind.DATASTORE])
                if sink and graph.nodes[sink].subtype in subtypes:
                    edges.append(Edge(sub.address, sink, EdgeKind.WRITE, Confidence.HIGH,
                                      f"{short(sub.address)} {config}.{attr} writes into {short(sink)}"))
        return edges
