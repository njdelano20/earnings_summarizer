"""
Run from the project folder:   py -m unittest discover -s tests -v
The Alpha Vantage key is a URL parameter, so it must never survive into an error message or a traceback.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import requests                                                # noqa: E402

import fetchers                                                # noqa: E402

KEY = "SECRETKEY123"


class Redaction(unittest.TestCase):
    def _fail_with(self, exc):
        with mock.patch("requests.get", side_effect=exc):
            with self.assertRaises(fetchers.AlphaVantageError) as ctx:
                fetchers._request("CPRT", "2026Q2", KEY)
        return ctx.exception

    def test_connection_error_message_is_scrubbed(self):
        err = self._fail_with(requests.ConnectionError(
            f"HTTPSConnectionPool: Max retries exceeded with url: /query?function=X&apikey={KEY}&symbol=CPRT"))
        self.assertNotIn(KEY, str(err))
        self.assertIn("***", str(err))
        self.assertIsNone(err.__cause__)                       # the original exception (with the URL) is not chained
        self.assertTrue(err.__suppress_context__)

    def test_http_error_is_scrubbed(self):
        resp = mock.Mock()
        resp.raise_for_status.side_effect = requests.HTTPError(f"500 Server Error for url: https://x/query?apikey={KEY}")
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(fetchers.AlphaVantageError) as ctx:
                fetchers._request("CPRT", "2026Q2", KEY)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_non_json_body_is_scrubbed(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("no json")
        resp.text = f"<html>echo apikey={KEY}</html>"
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(fetchers.AlphaVantageError) as ctx:
                fetchers._request("CPRT", "2026Q2", KEY)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_the_apis_own_rate_limit_reply_quotes_the_key_and_is_scrubbed(self):
        # Alpha Vantage: "We have detected your API key as <KEY> and our standard API rate limit is 25 requests per day."
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"Information": f"We have detected your API key as {KEY} and our standard API rate limit "
                                                  f"is 25 requests per day."}
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(fetchers.AlphaVantageError) as ctx:
                fetchers._request("CPRT", "2026Q2", KEY)
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIn("rate limit", str(ctx.exception))                # the useful part survives

    def test_scrub_secrets_catches_a_key_it_was_never_told_about(self):
        out = fetchers.scrub_secrets("We have detected your API key as ABCDEF1234567890 and more; also apikey=ZZZZ9999999999")
        self.assertNotIn("ABCDEF1234567890", out)
        self.assertNotIn("ZZZZ9999999999", out)

    def test_batch_log_and_state_never_hold_a_key(self):
        import io
        import contextlib
        import tempfile
        import batch
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                batch.log("boom: we have detected your API key as SECRETKEY99999999 sorry", Path(d))
            self.assertNotIn("SECRETKEY99999999", out.getvalue())
            self.assertNotIn("SECRETKEY99999999", (Path(d) / "batch.log").read_text(encoding="utf-8"))

    def test_api_information_message_still_raises_normally(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"Information": "rate limit reached"}
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(fetchers.AlphaVantageError) as ctx:
                fetchers._request("CPRT", "2026Q2", KEY)
        self.assertIn("rate limit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
