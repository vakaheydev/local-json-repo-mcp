import json
import tempfile
import unittest
from pathlib import Path

import server


class RepositorySearchTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        server.REPO_PATH = Path(self.temp_dir.name)

        self._write(
            "payments.Api.json",
            {
                "id": "api-123",
                "name": "Payments API",
                "proxy": {"virtual_hosts": [{"path": "/payments/v1"}]},
                "endpoint": {"target": "https://payments.internal"},
            },
        )
        self._write(
            "mobile.Application.json",
            {
                "id": "app-123",
                "name": "Mobile Banking",
                "settings": {"oauth": {"client_id": "mobile-client"}},
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, name, data):
        (server.REPO_PATH / name).write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_search_api_by_name(self):
        result = server.search_api_by_name("payment")
        self.assertEqual(result[0]["id"], "api-123")

    def test_search_api_by_path(self):
        result = server.search_api_by_path("/payments/v1")
        self.assertEqual(result[0]["name"], "Payments API")

    def test_search_application(self):
        self.assertEqual(
            server.search_application_by_name("mobile")[0]["id"], "app-123"
        )
        self.assertEqual(
            server.search_application_by_client_id("mobile-client")[0]["id"],
            "app-123",
        )

    def test_search_string_anywhere(self):
        result = server.search_repository("payments.internal")
        self.assertEqual(result[0]["path"], "payments.Api.json")


if __name__ == "__main__":
    unittest.main()
