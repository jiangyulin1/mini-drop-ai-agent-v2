from server.app.diagnosis.v6_policy import verify_primary_confirmation


def test_downstream_anomaly_without_supported_causal_edge_cannot_be_confirmed_primary():
    graph = {
        "nodes": [{
            "node_id": "downstream-db",
            "verifier_role": "PRIMARY_CAUSE",
        }],
        "edges": [{
            "source_node_id": "downstream-db",
            "target_node_id": "checkout",
            "verification_state": "UNVERIFIED",
        }],
    }
    assert verify_primary_confirmation(graph, "CONFIRMED") == "PARTIALLY_CONFIRMED"
    graph["edges"][0]["verification_state"] = "SUPPORTED"
    assert verify_primary_confirmation(graph, "CONFIRMED") == "CONFIRMED"


def test_blocker_gap_or_undistinguished_alternative_forces_downgrade():
    graph = {
        "nodes": [{"node_id": "cpu", "verifier_role": "PRIMARY_CAUSE"}],
        "edges": [],
    }
    assert verify_primary_confirmation(graph, "CONFIRMED", blocker_gaps=1) == "PARTIALLY_CONFIRMED"
    assert verify_primary_confirmation(
        graph,
        "CONFIRMED",
        alternative_primary_not_distinguished=True,
    ) == "PARTIALLY_CONFIRMED"
