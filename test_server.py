import unittest

from server import _compact, _first, _recursive_string_match


class ServerHelpersTest(unittest.TestCase):
    def test_first(self):
        data = {"metadata": {"id": "api-123"}}
        self.assertEqual(_first(data, ["id", "metadata.id"]), "api-123")

    def test_recursive_string_match(self):
        data = {"proxy": {"virtual_hosts": [{"path": "/payments/v1"}]}}
        self.assertTrue(_recursive_string_match(data, "payments"))

    def test_compact(self):
        self.assertEqual(_compact(["a", "a", "b"], limit=2), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
