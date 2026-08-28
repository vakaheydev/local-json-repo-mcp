import os
import unittest


REPO_PATH = os.getenv("TEST_GRAVITEE_REPOSITORY_PATH")
if not REPO_PATH:
    raise RuntimeError("TEST_GRAVITEE_REPOSITORY_PATH is not configured")

# server.py creates/refreshes managed worktrees during import.
os.environ["GRAVITEE_REPOSITORY_PATH"] = REPO_PATH

import server


class RepositorySearchTest(unittest.TestCase):
    def test_search_api_by_name(self):
        sample = server.list_apis(scope="test_int", limit=1)
        self.assertTrue(sample)
        name = sample[0].get("name")
        self.assertTrue(name)
        result = server.search_api_by_name(name, scope="test_int")
        self.assertTrue(result)

    def test_search_api_by_path(self):
        sample = server.list_apis(scope="test_int", limit=20)
        path = next(
            (
                value
                for api in sample
                for value in api.get("paths", [])
                if isinstance(value, str) and value
            ),
            None,
        )
        if path is None:
            self.skipTest("No API path found in test_int")
        self.assertTrue(server.search_api_by_path(path, scope="test_int"))

    def test_search_application(self):
        sample = server.list_applications(scope="all", limit=1)
        if not sample:
            self.skipTest("No applications found")
        name = sample[0].get("name")
        if not name:
            self.skipTest("Application has no name")
        self.assertTrue(server.search_application_by_name(name, scope="all"))

    def test_search_string_anywhere(self):
        sample = server.list_apis(scope="test_int", limit=1)
        self.assertTrue(sample)
        api_id = sample[0].get("id")
        if not api_id:
            self.skipTest("API has no id")
        self.assertTrue(server.search_repository(api_id, scope="test_int"))


if __name__ == "__main__":
    unittest.main()
