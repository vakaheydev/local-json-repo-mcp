import os
import unittest
from pathlib import Path

import server


REPO_PATH = os.getenv("TEST_GRAVITEE_REPOSITORY_PATH")

if not REPO_PATH:
    raise RuntimeError(
        "TEST_GRAVITEE_REPOSITORY_PATH is not configured"
    )

server.REPO_PATH = Path(REPO_PATH).expanduser().resolve()


class RepositorySearchTest(unittest.TestCase):
    def test_search_api_by_name(self):
        result = server.search_api_by_name("payment")
        self.assertGreater(len(result), 0)

    def test_search_api_by_path(self):
        result = server.search_api_by_path("/payments")
        self.assertGreater(len(result), 0)

    def test_search_application(self):
        result = server.search_application_by_name("mobile")
        self.assertGreater(len(result), 0)

    def test_search_string_anywhere(self):
        result = server.search_repository("client_id")
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
