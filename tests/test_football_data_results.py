"""Tests de la gestion des limites API football-data.org."""
import io
import unittest
from email.message import Message
from unittest.mock import patch
import urllib.error

import football_data_results as collector


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"matches": []}'


class FootballDataRetryTests(unittest.TestCase):
    def test_retry_delay_uses_retry_after_header(self):
        headers = Message()
        headers["Retry-After"] = "17"
        error = urllib.error.HTTPError("https://example.test", 429, "limited",
                                       headers, io.BytesIO(b"{}"))
        self.assertEqual(collector.retry_delay_seconds(error), 17.0)

    def test_retry_delay_parses_provider_message(self):
        error = urllib.error.HTTPError(
            "https://example.test", 429, "limited", Message(),
            io.BytesIO(b'{"message":"You reached your request limit. Wait 44 seconds."}')
        )
        self.assertEqual(collector.retry_delay_seconds(error), 44.0)

    def test_open_json_retries_http_429(self):
        error = urllib.error.HTTPError(
            "https://example.test", 429, "limited", Message(),
            io.BytesIO(b'{"message":"Wait 2 seconds."}')
        )
        with patch.object(collector.urllib.request, "urlopen",
                          side_effect=[error, FakeResponse()]) as mocked:
            with patch.object(collector.time, "sleep") as sleeper:
                result = collector.open_json_with_retry(object())
        self.assertEqual(result, {"matches": []})
        self.assertEqual(mocked.call_count, 2)
        sleeper.assert_called_once_with(2.0)

    def test_non_429_http_error_is_not_retried(self):
        error = urllib.error.HTTPError(
            "https://example.test", 401, "unauthorized", Message(),
            io.BytesIO(b"{}")
        )
        with patch.object(collector.urllib.request, "urlopen",
                          side_effect=error) as mocked:
            with self.assertRaises(urllib.error.HTTPError):
                collector.open_json_with_retry(object())
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
