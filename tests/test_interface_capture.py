import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from multi_interface_test import verify_capture


@pytest.mark.parametrize("missing_data,loopback,expected", [(False, False, True),
                                                         (True, False, False),
                                                         (False, True, False)])
def test_direct_claim_requires_physical_capture_on_both_interfaces(tmp_path, missing_data, loopback, expected):
    endpoints = {"benchmark": ["192.0.2.1", 52031], "echo": ["10.0.0.2", 52032]}
    summaries, packets = {}, []
    for interface, role in enumerate(("benchmark", "echo")):
        local = endpoints[role]
        control = [local[0], 60000]
        summaries[role] = {"local_endpoint": local, "control_endpoint": control, "direct_received": 1}
        for kind, source, destination in (("register", local, ["203.0.113.1", 3478]),
                                          ("tcp", control, ["203.0.113.1", 8443]),
                                          ("quic", local, ["198.51.100.1", 2000]),
                                          ("quic", ["198.51.100.1", 2000], local)):
            if missing_data and interface == 1 and kind == "quic":
                continue
            packets.append({"interface_id": str(interface), "kind": kind, "source": source,
                            "destination": destination, "packets": 1})
    if loopback:
        packets.append({"interface_id": "2", "packets": 1})
    summary = {"cases": [{"benchmark_network": "ethernet", "echo_network": "hotspot", "summaries": summaries}]}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "packet-evidence.json").write_text(json.dumps(packets))
    verify_capture(tmp_path)
    result = json.loads((tmp_path / "summary.json").read_text())
    assert result["cases"][0]["physical_direct_path_verified"] is expected
