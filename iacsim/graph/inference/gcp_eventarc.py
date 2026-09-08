"""Inference rule: gcp_eventarc                                             [M11]

    google_eventarc_trigger.transport.pubsub.topic            → the source topic
    google_eventarc_trigger.matching_criteria[bucket]         → the source bucket
    google_eventarc_trigger.destination.{cloud_run_service.service, cloud_function,
        workflow, gke.cluster, http_endpoint.uri}             → the destination node
    google_cloudfunctions2_function.event_trigger.{pubsub_topic, event_filters[bucket]}
    google_cloudfunctions_function.event_trigger.resource     → a function's own trigger (v2 / v1)
    google_storage_notification.{bucket, topic}               → bucket —PUBLISH→ topic
    google_eventarc_enrollment.{message_bus, destination}     → bus —PUBLISH→ pipeline (Eventarc Advanced)
    google_eventarc_pipeline.destinations[].{http_endpoint.uri, workflow, message_bus, topic}

emits source —CONSUME→ destination for every trigger, HIGH confidence: the
trigger *is* the delivery configuration, and it is glue — the edge goes straight
from the event source to the service, the way an event source mapping does. A
destination named by a literal URL is matched by name (LOW, see `node_for_url`).
Evidence names the event type.

Deliberately left: a trigger whose source is a Cloud Audit Log (`serviceName` /
`methodName` criteria) or an Eventarc-created topic has no source node — no
edge; `google_eventarc_google_api_source` publishes Google API events into a bus
from no node in the graph; the trigger's own node (the normaliser keeps
`google_eventarc_trigger` as a QUEUE) is not on the path drawn here.
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import INFERENCE_RULES, InferenceRule
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, NodeKind, RawResource, RawResources
from iacsim.graph.inference._common import block, blocks, first_node, label_index, node_for_url, raws_of_type, short

SOURCE_KINDS = (NodeKind.QUEUE, NodeKind.DATASTORE)
DESTINATION_KINDS = (NodeKind.COMPUTE, NodeKind.ORCHESTRATOR)


@INFERENCE_RULES.register("gcp_eventarc")
class GcpEventarcRule(InferenceRule):
    def apply(self, graph: InfraGraph, raw: RawResources) -> list[Edge]:
        edges: list[Edge] = []
        for trigger in raws_of_type(raw, "google_eventarc_trigger"):
            source, event_type = _trigger_source(graph, trigger)
            if source is None:
                continue
            for target, confidence, how in _trigger_destinations(graph, trigger):
                edges.append(_delivery(trigger, source, target, confidence, event_type, how))
        for fn in raws_of_type(raw, "google_cloudfunctions2_function", "google_cloudfunctions_function"):
            if fn.address not in graph.nodes:
                continue
            source, event_type = _function_source(graph, fn)
            if source is not None:
                edges.append(_delivery(fn, source, fn.address, Confidence.HIGH, event_type, "its event_trigger"))
        edges.extend(_storage_notifications(graph, raw))
        edges.extend(_advanced(graph, raw))
        return edges


def _delivery(via: RawResource, source: str, target: str, confidence: Confidence, event_type: str, how: str) -> Edge:
    return Edge(source, target, EdgeKind.CONSUME, confidence,
                f"{short(via.address)} delivers {event_type} events from {short(source)} to {short(target)} ({how})")


# ------------------------------------------------------------------ google_eventarc_trigger

def _trigger_source(graph: InfraGraph, trigger: RawResource) -> tuple[str | None, str]:
    criteria = {c.get("attribute"): c.get("value") for c in blocks(trigger.attrs.get("matching_criteria"))}
    event_type = criteria.get("type") if isinstance(criteria.get("type"), str) else "unknown"
    topic = block(block(trigger.attrs.get("transport")).get("pubsub")).get("topic")
    source = first_node(graph, topic, [NodeKind.QUEUE])
    if source is None and "bucket" in criteria:
        source = _resolve(graph, criteria["bucket"], [NodeKind.DATASTORE], ("gcs",))
    return source, event_type


def _trigger_destinations(graph: InfraGraph, trigger: RawResource) -> list[tuple[str, Confidence, str]]:
    dest = block(trigger.attrs.get("destination"))
    out: list[tuple[str, Confidence, str]] = []
    for attr, value in (("cloud_run_service.service", block(dest.get("cloud_run_service")).get("service")),
                        ("cloud_function", dest.get("cloud_function")),
                        ("workflow", dest.get("workflow")),
                        ("gke.cluster", block(dest.get("gke")).get("cluster"))):
        target = first_node(graph, value, DESTINATION_KINDS)
        if target:
            out.append((target, Confidence.HIGH, f"destination.{attr}"))
    uri = block(dest.get("http_endpoint")).get("uri")
    if (hit := node_for_url(graph, uri)) is not None:
        out.append((hit[0], hit[1], f"destination.http_endpoint.uri is {hit[2]}"))
    return out


# ------------------------------------------------------------------ a function's own trigger

def _function_source(graph: InfraGraph, fn: RawResource) -> tuple[str | None, str]:
    trigger = block(fn.attrs.get("event_trigger"))
    if not trigger:
        return None, ""
    event_type = trigger.get("event_type") if isinstance(trigger.get("event_type"), str) else "unknown"
    source = first_node(graph, trigger.get("pubsub_topic"), [NodeKind.QUEUE])              # v2
    if source is None:
        filters = {f.get("attribute"): f.get("value") for f in blocks(trigger.get("event_filters"))}
        if "bucket" in filters:
            source = _resolve(graph, filters["bucket"], [NodeKind.DATASTORE], ("gcs",))
    if source is None and "resource" in trigger:                                           # v1
        subtypes = ("gcs",) if "storage" in event_type else ("pubsub",)
        source = _resolve(graph, trigger["resource"], SOURCE_KINDS, subtypes)
    return source, event_type


# ------------------------------------------------------------------ storage notifications, Eventarc Advanced

def _storage_notifications(graph: InfraGraph, raw: RawResources) -> list[Edge]:
    edges: list[Edge] = []
    for note in raws_of_type(raw, "google_storage_notification"):
        bucket = _resolve(graph, note.attrs.get("bucket"), [NodeKind.DATASTORE], ("gcs",))
        topic = _resolve(graph, note.attrs.get("topic"), [NodeKind.QUEUE], ("pubsub",))
        if bucket and topic:
            events = note.attrs.get("event_types")
            what = ", ".join(e for e in events if isinstance(e, str)) if isinstance(events, list) else "object"
            edges.append(Edge(bucket, topic, EdgeKind.PUBLISH, Confidence.HIGH,
                              f"{short(note.address)} publishes {what} events from {short(bucket)} to {short(topic)}"))
    return edges


def _advanced(graph: InfraGraph, raw: RawResources) -> list[Edge]:
    edges: list[Edge] = []
    for enrollment in raws_of_type(raw, "google_eventarc_enrollment"):
        bus = first_node(graph, enrollment.attrs.get("message_bus"), [NodeKind.QUEUE])
        pipeline = first_node(graph, enrollment.attrs.get("destination"), [NodeKind.QUEUE])
        if bus and pipeline and bus != pipeline:
            match = enrollment.attrs.get("cel_match")
            where = f" matching {match}" if isinstance(match, str) else ""
            edges.append(Edge(bus, pipeline, EdgeKind.PUBLISH, Confidence.HIGH,
                              f"{short(enrollment.address)} enrols {short(pipeline)} on {short(bus)}{where}"))
    for pipeline in raws_of_type(raw, "google_eventarc_pipeline"):
        if pipeline.address not in graph.nodes:
            continue
        for dest in blocks(pipeline.attrs.get("destinations")):
            for attr, target, confidence, how in _pipeline_targets(graph, dest):
                kind = EdgeKind.PUBLISH if graph.nodes[target].kind == NodeKind.QUEUE else EdgeKind.CONSUME
                edges.append(Edge(pipeline.address, target, kind, confidence,
                                  f"{short(pipeline.address)} destinations.{attr} is {how} for {short(target)}"))
    return edges


def _pipeline_targets(graph: InfraGraph, dest: dict[str, Any]) -> list[tuple[str, str, Confidence, str]]:
    out: list[tuple[str, str, Confidence, str]] = []
    if (hit := node_for_url(graph, block(dest.get("http_endpoint")).get("uri"))) is not None:
        out.append(("http_endpoint.uri", hit[0], hit[1], hit[2]))
    named = (("workflow", DESTINATION_KINDS), ("message_bus", (NodeKind.QUEUE,)), ("topic", (NodeKind.QUEUE,)))
    for attr, kinds in named:
        target = first_node(graph, dest.get(attr), kinds)
        if target:
            out.append((attr, target, Confidence.HIGH, "a placeholder"))
    return out


def _resolve(graph: InfraGraph, value: Any, kinds: tuple[NodeKind, ...] | list[NodeKind],
             subtypes: tuple[str, ...]) -> str | None:
    """A placeholder, else a literal name (`"my-topic"`, `projects/p/topics/my-topic`)
    matched against the labels of nodes with the given subtypes."""
    if (target := first_node(graph, value, kinds)) is not None:
        return target
    if isinstance(value, str) and "${" not in value:
        ids = label_index(graph, subtypes=subtypes).get(value.rsplit("/", 1)[-1])
        return ids[0] if ids else None
    return None
