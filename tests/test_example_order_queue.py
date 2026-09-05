"""examples/order-queue: API GW → Lambda → SQS → Lambda (event source mapping) → DynamoDB.
The first fixture where PUBLISH / CONSUME edges and the event_source_mapping rule run."""

import pytest
from conftest import edge, result

from iacsim.core.models import EdgeKind

API_GW = "aws_api_gateway_rest_api.main"
API = "module.api.aws_lambda_function.this"
WORKER = "module.worker.aws_lambda_function.this"
QUEUE = "aws_sqs_queue.orders"
TABLE = "module.orders.aws_dynamodb_table.this"


def rules(e):
    return set((e.rule or "").split("+"))


def test_graph_shape_and_edges(order_queue):
    graph, raw = order_queue
    assert raw.warnings == [] and graph.warnings == []
    pub = edge(graph, API, QUEUE)
    assert pub.kind == EdgeKind.PUBLISH and pub.ops == [EdgeKind.PUBLISH] and rules(pub) == {"env_var", "iam_policy"}
    con = edge(graph, QUEUE, WORKER)
    assert con.kind == EdgeKind.CONSUME and rules(con) == {"event_source_mapping"}
    assert "orders_to_worker" in con.evidence
    tab = edge(graph, WORKER, TABLE)
    assert tab.kind == EdgeKind.READ and tab.ops == [EdgeKind.READ, EdgeKind.WRITE]
    # the worker's sqs:ReceiveMessage grant is a consumer, not a publisher: no worker → queue edge
    assert graph.find_edge(WORKER, QUEUE) is None
    assert graph.find_edge(QUEUE, "aws_sqs_queue.dlq") is None   # redrive is not a request hop


def test_place_order_is_the_synchronous_half(order_queue_run, default_profile):
    p = default_profile
    d, proc = p.distance, p.processing
    r = result(order_queue_run, "place_order")
    assert r.warnings == []
    lam = proc["lambda"]["defaults"]
    expected = (
        2 * d["internet_to_edge"] + proc["api_gateway"]["defaults"]["route"]          # internet → api gw
        + 2 * d["same_region_unknown_az"] + lam["warm"] + lam["cold"] * lam["cold_prob"]   # api gw → api
        + 1 * d["same_region_unknown_az"] + proc["sqs"]["defaults"]["publish"]         # api → queue, one-way
        + lam["respond"]                                                              # response leg
    )
    assert r.total_ms == pytest.approx(expected)
    publish = next(h for h in r.hops if h.dst == QUEUE)
    assert publish.breakdown["distance"] == pytest.approx(d["same_region_unknown_az"])   # not doubled


def test_process_order_charges_the_poll_delay_once(order_queue_run, default_profile):
    p = default_profile
    d, proc = p.distance, p.processing
    r = result(order_queue_run, "process_order")
    assert r.warnings == []
    lam = proc["lambda"]["defaults"]
    consume = next(h for h in r.hops if h.src == QUEUE and h.dst == WORKER)
    assert consume.breakdown["distance"] == pytest.approx(d["same_region_unknown_az"])
    assert consume.breakdown["processing"] == pytest.approx(proc["sqs"]["defaults"]["consume"] + lam["warm"])
    write = next(h for h in r.hops if h.dst == TABLE)
    assert write.breakdown["processing"] == pytest.approx(proc["dynamodb"]["defaults"]["write"])   # op: write
    assert r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops))
