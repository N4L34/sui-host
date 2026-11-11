import json
import unittest
from unittest.mock import patch
from egress.telemetry import Telemetry, fetch


class TelemetryTests(unittest.TestCase):
    def test_native_snapshot_exports_only_numeric_aggregates(self):
        class Response:
            status = 200

            def read(self, _):
                return json.dumps(
                    {
                        "uploadTotal": 12,
                        "downloadTotal": 34,
                        "connections": [
                            {
                                "metadata": {"host": "PRIVATE_CANARY"},
                                "chains": ["node-primary"],
                            }
                        ],
                    }
                ).encode()

        class Connection:
            def __init__(self, *a, **k):
                pass

            def request(self, *a):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with patch("egress.telemetry.http.client.HTTPConnection", Connection):
            state = Telemetry()
            state.refresh()
            snapshot = state.snapshot()
        self.assertEqual(snapshot["active_connections"], 1)
        self.assertEqual(snapshot["upload_bytes_total"], 12)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
