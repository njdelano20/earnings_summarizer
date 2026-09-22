"""
Run from the project folder:   py -m unittest discover -s tests -v
draft_gold.py: drafting a candidate gold file with a free LLM (Gemini), read-the-transcript-only, with the two
automatic checks (metric-in-vocabulary, where-found-verbatim) that run before anything is trusted. All network
calls are mocked here -- none of these tests need a real GEMINI_API_KEY or make a real request.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import draft_gold as D                                         # noqa: E402
from evaluate import load_transcript                            # noqa: E402
from annotate import find_call                                  # noqa: E402

KEY = "FAKEGEMINIKEY123456"


def hesm_transcript():
    return load_transcript(find_call("HESM_2026Q2"))


class Prompt(unittest.TestCase):
    def test_metric_vocabulary_includes_core_and_the_assigned_pack(self):
        metrics = D.available_metrics("HESM")
        self.assertIn("net_income", metrics)          # core
        self.assertIn("throughput", metrics)           # oil_gas_midstream pack
        self.assertNotIn("affo", metrics)               # a different pack's metric

    def test_unknown_ticker_gets_core_only(self):
        metrics = D.available_metrics("ZZZZNOTREAL")
        self.assertIn("net_income", metrics)
        self.assertNotIn("throughput", metrics)

    def test_prompt_lists_every_available_metric_and_the_transcript_text(self):
        t = hesm_transcript()
        metrics = D.available_metrics(t.meta.get("ticker"))
        prompt = D.build_prompt(t, metrics)
        for name in metrics:
            self.assertIn(name, prompt)
        self.assertIn("net income was $174 million", prompt)

    def test_management_text_excludes_operator_and_ir(self):
        t = hesm_transcript()
        text = D.management_text(t)
        self.assertNotIn("Good day, ladies and gentlemen", text)      # the operator's opening
        self.assertIn("net income was $174 million", text)             # the CFO


class Schema(unittest.TestCase):
    def test_schema_is_valid_json_with_the_right_metric_enum(self):
        schema = D._response_schema(["revenue", "net_income"])
        json.dumps(schema)  # must not raise
        self.assertEqual(schema["properties"]["expected"]["items"]["properties"]["metric"]["enum"],
                         ["revenue", "net_income"])
        self.assertIn("where", schema["properties"]["expected"]["items"]["required"])


class Filtering(unittest.TestCase):
    def setUp(self):
        self.t = hesm_transcript()
        self.metrics = D.available_metrics(self.t.meta.get("ticker"))
        self.text_lower = self.t.text.lower()

    def test_a_real_verbatim_claim_with_a_known_metric_is_kept(self):
        kept, rejected = D.filter_entries(
            [{"metric": "net_income", "where": "net income was $174 million", "value": 174e6}],
            self.metrics, self.text_lower)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, [])

    def test_a_hallucinated_where_phrase_is_rejected_not_silently_dropped(self):
        kept, rejected = D.filter_entries(
            [{"metric": "net_income", "where": "revenue soared to a trillion dollars this quarter", "value": 1}],
            self.metrics, self.text_lower)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("not found verbatim", rejected[0]["_rejected_reason"])
        self.assertEqual(rejected[0]["metric"], "net_income")          # the original entry is preserved, for review

    def test_a_metric_outside_the_calls_vocabulary_is_rejected(self):
        kept, rejected = D.filter_entries(
            [{"metric": "affo_per_share", "where": "net income was $174 million", "value": 1}],
            self.metrics, self.text_lower)
        self.assertEqual(kept, [])
        self.assertIn("not in this call's vocabulary", rejected[0]["_rejected_reason"])

    def test_a_blank_where_is_rejected(self):
        kept, rejected = D.filter_entries([{"metric": "net_income", "where": "", "value": 1}],
                                          self.metrics, self.text_lower)
        self.assertEqual(kept, [])

    def test_matching_is_case_insensitive(self):
        kept, _ = D.filter_entries(
            [{"metric": "net_income", "where": "NET INCOME WAS $174 MILLION", "value": 1}],
            self.metrics, self.text_lower)
        self.assertEqual(len(kept), 1)

    def test_a_physical_quantity_forced_into_usd_is_rejected(self):
        # the real 2026-09-22 incident: "121,000 barrels of water per day" came back as {"unit": "USD", "value": 121000}
        entry = {"kind": "reported", "metric": "throughput", "segment": "total", "stat": "level", "unit": "USD",
                 "value": 121000, "where": "121,000 barrels of water per day for water gathering"}
        kept, rejected = D.filter_entries([entry], self.metrics, self.text_lower)
        self.assertEqual(kept, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("physical quantity", rejected[0]["_rejected_reason"])

    def test_a_quantity_only_metric_reported_as_a_growth_rate_is_still_kept(self):
        entry = {"kind": "reported", "metric": "throughput", "segment": "total", "stat": "growth", "unit": "pct",
                 "value": 5, "where": "net income was $174 million"}          # any verbatim phrase for this test
        kept, rejected = D.filter_entries([entry], self.metrics, self.text_lower)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rejected, [])


class EndToEndMocked(unittest.TestCase):
    """draft() with requests.post mocked to return a Gemini-shaped response -- no real network call."""

    def _mock_response(self, expected, unmapped=None):
        payload = {"candidates": [{"content": {"parts": [
            {"text": json.dumps({"expected": expected, "unmapped": unmapped or []})}]}}]}
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def test_draft_end_to_end_with_a_mocked_gemini_response(self):
        good = {"kind": "reported", "metric": "net_income", "segment": "total", "stat": "level", "unit": "USD",
                "value": 174000000, "where": "net income was $174 million"}
        bad_metric = {"kind": "reported", "metric": "not_a_real_metric", "segment": "total", "stat": "level",
                     "unit": "USD", "value": 1, "where": "net income was $174 million"}
        bad_where = {"kind": "reported", "metric": "ebitda", "segment": "total", "stat": "level", "unit": "USD",
                    "value": 1, "where": "this sentence is not in the transcript"}
        with mock.patch("requests.post", return_value=self._mock_response([good, bad_metric, bad_where],
                                                                          unmapped=["throughput volumes in MMcf/d"])):
            result = D.draft("HESM_2026Q2", KEY)
        self.assertEqual(len(result["expected"]), 1)
        self.assertEqual(result["expected"][0]["metric"], "net_income")
        self.assertEqual(len(result["rejected_by_automatic_checks"]), 2)
        self.assertEqual(result["unmapped_claims_gemini_flagged"], ["throughput volumes in MMcf/d"])
        self.assertIn("UNVERIFIED DRAFT", result["notes"])
        self.assertEqual(result["transcript"], "data/raw/alphavantage/HESM_2026Q2.json")
        self.assertEqual(result["forbidden"], [])

    def test_write_draft_lands_in_gold_drafts_not_gold(self):
        with mock.patch("requests.post", return_value=self._mock_response([])):
            result = D.draft("HESM_2026Q2", KEY)
        with tempfile.TemporaryDirectory() as d:
            path = D.write_draft("HESM_2026Q2", result, Path(d))
            self.assertEqual(path, Path(d) / "HESM_2026Q2.json")
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["transcript"], result["transcript"])


class Errors(unittest.TestCase):
    def test_missing_key_prints_setup_instructions_and_exits_nonzero(self):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(sys, "argv", ["draft_gold.py", "HESM_2026Q2"]), \
             contextlib.redirect_stderr(out):
            code = D.main()
        self.assertEqual(code, 1)
        self.assertIn("aistudio.google.com", out.getvalue())
        self.assertNotIn("GEMINI_API_KEY=", out.getvalue())         # no accidental key echo (there is none set anyway)

    def test_network_failure_is_wrapped_and_the_key_is_scrubbed(self):
        import requests
        with mock.patch("requests.post", side_effect=requests.ConnectionError(f"failed, apikey={KEY} in url")), \
             mock.patch("time.sleep"):
            with self.assertRaises(D.GeminiError) as ctx:
                D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL, max_retries=1)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_a_transient_503_is_retried_and_then_succeeds(self):
        good = self._resp_ok([])
        overloaded = mock.Mock(status_code=503)
        with mock.patch("requests.post", side_effect=[overloaded, overloaded, good]), mock.patch("time.sleep") as sleep:
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL, max_retries=4, base_delay=1.0)
        self.assertEqual(out, {"expected": [], "unmapped": []})
        self.assertEqual(sleep.call_count, 2)                      # two retries before the success

    def test_repeated_503s_eventually_give_up_with_a_clear_error(self):
        overloaded = mock.Mock(status_code=503)
        overloaded.raise_for_status.side_effect = __import__("requests").HTTPError("503 Server Error")
        with mock.patch("requests.post", return_value=overloaded), mock.patch("time.sleep"):
            with self.assertRaises(D.GeminiError):
                D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL, max_retries=2, base_delay=0.1)

    def _resp_ok(self, expected, unmapped=None):
        payload = {"candidates": [{"content": {"parts": [
            {"text": json.dumps({"expected": expected, "unmapped": unmapped or []})}]}}]}
        resp = mock.Mock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def test_gemini_error_payload_is_scrubbed(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"error": {"message": f"invalid api key as {KEY}"}}
        with mock.patch("requests.post", return_value=resp):
            with self.assertRaises(D.GeminiError) as ctx:
                D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_list_models_returns_names_without_the_models_prefix(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"models": [
            {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embed", "supportedGenerationMethods": ["embedContent"]}]}
        with mock.patch("requests.get", return_value=resp):
            names = D.list_models(KEY)
        self.assertEqual(names, ["gemini-flash-latest"])          # embed-only model excluded

    def test_malformed_json_from_the_model_is_a_clear_error(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "not json { at all"}]}}]}
        with mock.patch("requests.post", return_value=resp):
            with self.assertRaises(D.GeminiError):
                D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)

    def test_structured_mode_failing_falls_back_to_plain_json_and_still_succeeds(self):
        overloaded = mock.Mock(status_code=503)
        overloaded.raise_for_status.side_effect = __import__("requests").HTTPError("503")
        fallback_ok = self._resp_ok([])
        # the structured attempt (with schema) always 503s; the fallback (no schema) succeeds on its first try
        with mock.patch("requests.post", side_effect=[overloaded, fallback_ok]), mock.patch("time.sleep"):
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL, max_retries=0)
        self.assertEqual(out, {"expected": [], "unmapped": []})

    def _resp_ok(self, expected, unmapped=None):
        payload = {"candidates": [{"content": {"parts": [
            {"text": json.dumps({"expected": expected, "unmapped": unmapped or []})}]}}]}
        resp = mock.Mock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp


class MarkdownFencedJson(unittest.TestCase):
    def _resp(self, text):
        resp = mock.Mock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return resp

    def test_fenced_with_json_language_tag(self):
        text = '```json\n{"expected": [], "unmapped": []}\n```'
        with mock.patch("requests.post", return_value=self._resp(text)):
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertEqual(out, {"expected": [], "unmapped": []})

    def test_fenced_without_language_tag(self):
        text = '```\n{"expected": [], "unmapped": []}\n```'
        with mock.patch("requests.post", return_value=self._resp(text)):
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertEqual(out, {"expected": [], "unmapped": []})

    def test_plain_unfenced_json_still_works(self):
        with mock.patch("requests.post", return_value=self._resp('{"expected": [], "unmapped": []}')):
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertEqual(out, {"expected": [], "unmapped": []})

    def test_json_with_commentary_around_it_is_recovered_via_the_last_resort_brace_match(self):
        text = 'Here is the JSON you asked for:\n{"expected": [], "unmapped": []}\nLet me know if you need anything else!'
        with mock.patch("requests.post", return_value=self._resp(text)):
            out = D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertEqual(out, {"expected": [], "unmapped": []})


class DailyQuota(unittest.TestCase):
    """The 2026-09-22 incident: gemini-flash-latest's free tier turned out to be 20 requests/DAY, and blind
    retrying (plus a schema-less fallback against the SAME model) just burned through it faster. These pin the fix."""

    def _daily_quota_resp(self):
        resp = mock.Mock(status_code=429)
        resp.json.return_value = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded ... Please retry in 59s.",
            "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
        resp.raise_for_status.side_effect = __import__("requests").HTTPError("429")
        return resp

    def test_quota_is_daily_detects_the_real_response_shape(self):
        self.assertTrue(D._quota_is_daily(self._daily_quota_resp()))

    def test_quota_is_daily_is_false_for_an_ordinary_429_with_no_quota_details(self):
        resp = mock.Mock(status_code=429)
        resp.json.return_value = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "slow down"}}
        self.assertFalse(D._quota_is_daily(resp))

    def test_a_daily_quota_429_is_not_retried_at_all(self):
        with mock.patch("requests.post", return_value=self._daily_quota_resp()) as post, mock.patch("time.sleep") as sleep:
            with self.assertRaises(D.QuotaExhaustedError):
                D._post_once("prompt", KEY, D.DEFAULT_MODEL, {"type": "OBJECT"}, 30, max_retries=4, base_delay=1.0, scrub=str)
        self.assertEqual(post.call_count, 1)      # no retry burned against an exhausted daily quota
        sleep.assert_not_called()

    def test_call_gemini_does_not_attempt_the_schema_less_fallback_on_a_daily_quota_error(self):
        with mock.patch("requests.post", return_value=self._daily_quota_resp()) as post, mock.patch("time.sleep"):
            with self.assertRaises(D.QuotaExhaustedError) as ctx:
                D.call_gemini("prompt", {"type": "OBJECT"}, KEY, D.DEFAULT_MODEL)
        self.assertEqual(post.call_count, 1)      # the fallback (a second full request) never happens
        self.assertNotIn(KEY, str(ctx.exception))

    def test_a_plain_rate_limited_429_still_retries_normally(self):
        # a per-minute-style 429 with no daily-quota body: should NOT be treated as unrecoverable
        busy = mock.Mock(status_code=429)
        busy.json.return_value = {"error": {"code": 429, "message": "rate limited, try again shortly"}}
        good = self._resp_ok_static()
        with mock.patch("requests.post", side_effect=[busy, good]), mock.patch("time.sleep") as sleep:
            out = D._post_once("prompt", KEY, D.DEFAULT_MODEL, {"type": "OBJECT"}, 30, max_retries=2, base_delay=1.0, scrub=str)
        self.assertEqual(out, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
        self.assertEqual(sleep.call_count, 1)

    @staticmethod
    def _resp_ok_static():
        resp = mock.Mock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        return resp


class VerifySample(unittest.TestCase):
    def test_sample_is_reproducible_and_never_exceeds_the_pool(self):
        entries = [{"kind": "reported", "metric": "revenue", "segment": "total", "stat": "level", "unit": "USD",
                   "value": i, "where": f"claim {i}"} for i in range(3)]
        out1, out2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out1):
            D.print_verify_sample({"expected": entries}, n=10, seed="X")
        with contextlib.redirect_stdout(out2):
            D.print_verify_sample({"expected": entries}, n=10, seed="X")
        self.assertEqual(out1.getvalue(), out2.getvalue())            # same seed -> same sample
        self.assertIn("3 of 3", out1.getvalue())

    def test_empty_pool_does_not_crash(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            D.print_verify_sample({"expected": []}, n=5, seed="X")
        self.assertIn("Nothing survived", out.getvalue())


if __name__ == "__main__":
    unittest.main()
