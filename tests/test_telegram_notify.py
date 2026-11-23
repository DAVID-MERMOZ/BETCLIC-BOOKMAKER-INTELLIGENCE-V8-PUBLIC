import unittest
from html.parser import HTMLParser
from unittest.mock import Mock
from telegram_notify import build_message, format_kickoff, send_telegram


class TelegramNotificationTests(unittest.TestCase):
    def test_message_starts_each_fixture_with_competition_date_and_time(self):
        message = build_message([{
            "competition_name": "Ligue 1",
            "match_name": "Alpha - Beta",
            "kickoff_utc": "2026-11-23T18:30:00+00:00",
            "prediction": "HOME",
            "prob_home": "0.61",
            "prob_draw": "0.24",
            "prob_away": "0.15",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "over_1_5": "0.82",
            "over_2_5": "0.57",
            "over_3_5": "0.31",
            "btts_yes": "0.52",
            "market_prob_home": "0.55",
            "market_prob_draw": "0.25",
            "market_prob_away": "0.20",
            "max_abs_edge_pp": "6.0",
        }])
        self.assertLess(message.index("Championnat : Ligue 1"),
                        message.index("Alpha - Beta"))
        self.assertIn("Date : 23/11/2026", message)
        self.assertIn("Heure : 19:30", message)  # UTC+1, Cameroon
        self.assertIn("1 61.0%", message)
        self.assertIn("NO_BET_MODEL_UNVALIDATED", message)
        self.assertIn("Over 2,5 : 57.0%", message)


    def test_daily_sample_prediction_schema_is_supported(self):
        message = build_message([{
            "competition_name": "Ligue 1",
            "match_name": "Alpha - Beta",
            "kickoff_utc": "2026-11-23T18:30:00+00:00",
            "prediction_1x2": "DRAW",
            "prob_home": "0.30",
            "prob_draw": "0.45",
            "prob_away": "0.25",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "over_2_5": "0.56",
            "btts_yes": "0.51",
        }])
        self.assertIn("Pronostic modèle : <b>N</b>", message)
        self.assertIn("Probabilités modèle (1/N/2) : 1 30.0% / N 45.0% / 2 25.0%", message)
        self.assertIn("Championnat : Ligue 1", message)
        self.assertIn("Over 2,5 : 56.0%", message)

    def test_kickoff_handles_missing_and_invalid_values(self):
        self.assertIn("non renseignée", format_kickoff(""))
        self.assertIn("invalide", format_kickoff("not-a-date"))

    def test_news_findings_are_safe_html_links(self):
        message = build_message([{
            "competition_name": "Ligue <A>",
            "match_name": "Alpha - Beta",
            "kickoff_utc": "2026-11-23T18:30:00Z",
            "web_research_status": "RESULTS_FOUND",
            "web_research_findings_json": '[{"title":"Lineup update","source_name":"Example","url":"https://example.com/news?a=1&b=2"}]'
        }])
        self.assertIn("Ligue &lt;A&gt;", message)
        self.assertIn('href="https://example.com/news?a=1&amp;b=2"', message)

    def test_empty_predictions_are_explicit(self):
        self.assertIn("Aucune prédiction exploitable", build_message([]))

    def test_send_telegram_uses_api_and_checks_response(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock(return_value=response)
        self.assertTrue(send_telegram("test", "secret-token", "123", opener=opener))
        request = opener.call_args.args[0]
        self.assertIn("secret-token", request.full_url)
        self.assertEqual(request.get_header("Content-type"), "application/json")


    def test_long_message_is_truncated_only_between_complete_html_blocks(self):
        class BalanceChecker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.errors = []
            def handle_starttag(self, tag, attrs):
                if tag not in {"br", "hr", "img", "meta", "link", "input"}:
                    self.stack.append(tag)
            def handle_endtag(self, tag):
                if not self.stack or self.stack[-1] != tag:
                    self.errors.append(tag)
                else:
                    self.stack.pop()

        rows = [{
            "competition_name": "Championnat " + str(i),
            "match_name": "Equipe A " + ("X" * 500) + " - Equipe B",
            "kickoff_utc": "2026-11-24T18:00:00Z",
            "prediction_1x2": "HOME",
            "prob_home": "0.60", "prob_draw": "0.25", "prob_away": "0.15",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "web_research_status": "RESULTS_FOUND",
            "web_research_findings_json": '[{"title":"' + ("Actualite " * 50) + '","source_name":"Source","url":"https://example.com"}]'
        } for i in range(20)]
        message = build_message(rows)
        checker = BalanceChecker()
        checker.feed(message)
        checker.close()
        self.assertLessEqual(len(message), 4096)
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])


if __name__ == "__main__":
    unittest.main()


class TelegramHybridDedupTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from pathlib import Path
        self.state_path = Path(self.temp.name) / "telegram_state.json"
        self.rows = [{
            "match_id": "fixture-42",
            "competition_name": "Ligue 1",
            "match_name": "Alpha - Beta",
            "kickoff_utc": "2026-11-24T18:00:00+00:00",
            "prediction_1x2": "HOME",
            "prob_home": "0.61234",
            "prob_draw": "0.24001",
            "prob_away": "0.14765",
            "market_prob_home": "0.55",
            "market_prob_draw": "0.25",
            "market_prob_away": "0.20",
            "max_abs_edge_pp": "6.2",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "model_status": "EXPERIMENTAL",
            "market_status": "AVAILABLE",
            "comparison_status": "NO_LARGE_DIVERGENCE",
            "over_2_5": "0.57",
            "btts_yes": "0.52",
        }]

    def _process(self, rows, sender):
        from telegram_dedup import process_predictions
        return process_predictions(
            rows, "token", "chat", "https://example.test/run",
            "todays_sample_predictions.csv",
            lambda data, url, revision=False: ("REVISION:" if revision else "FIRST:") + str(data),
            sender, state_path=self.state_path, commit_to_git=False,
        )

    def test_first_table_is_sent_once_and_identical_table_is_skipped(self):
        sender = Mock()
        first = self._process(self.rows, sender)
        second = self._process(self.rows, sender)
        self.assertEqual(first["status"], "FIRST_TABLE_SENT")
        self.assertEqual(second["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 1)

    def test_real_prediction_change_sends_revision(self):
        sender = Mock()
        self._process(self.rows, sender)
        changed = [dict(self.rows[0], prediction_1x2="DRAW",
                        prob_home="0.20", prob_draw="0.60", prob_away="0.20")]
        result = self._process(changed, sender)
        self.assertEqual(result["status"], "REVISION_SENT")
        self.assertEqual(sender.call_count, 2)
        self.assertTrue(sender.call_args.args[0].startswith("REVISION:"))

    def test_metadata_and_row_order_only_do_not_trigger_message(self):
        sender = Mock()
        self._process(self.rows, sender)
        metadata_only = [dict(self.rows[0], generated_at_utc="2099-01-01T00:00:00Z",
                              training_n="999", run_id="different")]
        result = self._process(metadata_only, sender)
        self.assertEqual(result["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 1)

    def test_failed_telegram_send_does_not_advance_state_and_can_retry(self):
        failing = Mock(side_effect=RuntimeError("temporary Telegram failure"))
        with self.assertRaises(RuntimeError):
            self._process(self.rows, failing)
        sender = Mock()
        result = self._process(self.rows, sender)
        self.assertEqual(result["status"], "FIRST_TABLE_SENT")
        self.assertEqual(sender.call_count, 1)

    def test_repeated_concurrent_workflow_result_is_idempotent_after_first_commit(self):
        sender = Mock()
        self._process(self.rows, sender)
        # Both workflows share one GitHub Actions concurrency group; a later
        # serialized execution must observe the persisted state and skip.
        result = self._process(list(reversed(self.rows)), sender)
        self.assertEqual(result["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 1)
