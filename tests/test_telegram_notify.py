import unittest
import json
from html.parser import HTMLParser
from unittest.mock import Mock
from telegram_notify import build_message, build_messages, format_kickoff, send_telegram


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
            "model_status": "EXPERIMENTAL_NOT_VALIDATED",
            "validation_status": "TEAM_FORM_WALK_FORWARD_EXPERIMENT_COMPLETED",
            "calibration_status": "DESCRIPTIVE_ECE_ONLY_NOT_CALIBRATED",
            "lambda_home": "1.45",
            "lambda_away": "1.10",
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
        self.assertIn("Buts attendus (modèle) : domicile 1.45 | extérieur 1.10", message)
        self.assertIn("calibration DESCRIPTIVE_ECE_ONLY_NOT_CALIBRATED", message)


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

    def test_stale_market_snapshot_is_explicit_and_not_compared(self):
        message = build_message([{
            "competition_name": "Ligue 1",
            "match_name": "Alpha - Beta",
            "kickoff_utc": "2026-11-25T18:00:00Z",
            "prediction_1x2": "HOME",
            "prob_home": "0.60",
            "prob_draw": "0.25",
            "prob_away": "0.15",
            "market_status": "STALE_PREMATCH_1X2_SNAPSHOT",
            "market_snapshot_age_minutes": "1342.8",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
        }])
        self.assertIn("snapshot périmé (1343 min)", message)
        self.assertIn("comparaison ignorée", message)
        self.assertNotIn("Marché Betclic 1/N/2 : non disponible", message)

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
        with self.assertRaises(ValueError):
            build_message(rows)
        messages = build_messages(rows)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 4096 for message in messages))
        self.assertIn("1/", messages[0])
        for message in messages:
            checker = BalanceChecker()
            checker.feed(message)
            checker.close()
            self.assertEqual(checker.errors, [])
            self.assertEqual(checker.stack, [])


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
            "lambda_home": "1.45",
            "lambda_away": "1.10",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "model_status": "EXPERIMENTAL_NOT_VALIDATED",
            "validation_status": "TEAM_FORM_WALK_FORWARD_EXPERIMENT_COMPLETED",
            "calibration_status": "DESCRIPTIVE_ECE_ONLY_NOT_CALIBRATED",
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
        second = dict(
            self.rows[0], match_id="fixture-43", match_name="Beta - Gamma",
            kickoff_utc="2026-11-25T18:00:00+00:00",
        )
        initial = [
            dict(self.rows[0], web_research_status="NO_RECENT_RESULTS",
                 web_research_findings_json="[]"),
            second,
        ]
        self._process(initial, sender)
        metadata_only = [
            dict(
                second,
                generated_at_utc="2099-01-01T00:00:00Z",
                training_n="999",
                run_id="different",
                web_research_status="SEARCH_ERROR",
                web_research_findings_json='[{"title":"new search metadata"}]',
            ),
            dict(
                self.rows[0],
                generated_at_utc="2099-01-01T00:00:00Z",
                training_n="999",
                run_id="different",
                web_research_status="SEARCH_ERROR",
                web_research_findings_json='[{"title":"new search metadata"}]',
            ),
        ]
        result = self._process(metadata_only, sender)
        self.assertEqual(result["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 1)

    def test_equivalent_kickoff_and_display_rounding_do_not_trigger_revision(self):
        sender = Mock()
        self._process(self.rows, sender)
        equivalent = [dict(
            self.rows[0],
            kickoff_utc="2026-11-24T19:00:00+01:00",
            prob_home="0.61231",
            prob_draw="0.24002",
            prob_away="0.14767",
            lambda_home="1.4501",
        )]
        result = self._process(equivalent, sender)
        self.assertEqual(result["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 1)

    def test_visible_market_movement_change_triggers_revision(self):
        sender = Mock()
        self._process(self.rows, sender)
        changed = [dict(
            self.rows[0],
            market_movement_status="SIGNIFICANT_MOVE",
            market_movement_pattern="HOME_SHORTENING",
        )]
        result = self._process(changed, sender)
        self.assertEqual(result["status"], "REVISION_SENT")
        self.assertEqual(sender.call_count, 2)

    def test_market_probability_change_triggers_revision(self):
        sender = Mock()
        self._process(self.rows, sender)
        changed = [dict(
            self.rows[0],
            market_prob_home="0.56",
            market_prob_draw="0.24",
            market_prob_away="0.20",
        )]
        result = self._process(changed, sender)
        self.assertEqual(result["status"], "REVISION_SENT")
        self.assertEqual(sender.call_count, 2)

    def test_expected_goals_change_triggers_revision(self):
        sender = Mock()
        self._process(self.rows, sender)
        changed = [dict(self.rows[0], lambda_home="1.60")]
        result = self._process(changed, sender)
        self.assertEqual(result["status"], "REVISION_SENT")
        self.assertEqual(sender.call_count, 2)

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


    def test_persisted_state_contains_only_digest_and_count(self):
        sender = Mock()
        self._process(self.rows, sender)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        entry = state["sources"]["todays_sample_predictions.csv"]
        self.assertEqual(state["version"], 2)
        self.assertEqual(set(entry), {"signature", "match_count", "sent_at_utc"})
        self.assertEqual(entry["match_count"], 1)
        serialized = json.dumps(state)
        self.assertNotIn("fixture-42", serialized)
        self.assertNotIn("Alpha - Beta", serialized)
        self.assertNotIn("prob_home", serialized)

    def test_legacy_state_migrates_without_resending_or_losing_signature(self):
        from telegram_dedup import canonical_prediction_table, _signature

        canonical = canonical_prediction_table(self.rows)
        signature = _signature(canonical)
        legacy = {
            "version": 1,
            "sources": {
                "todays_sample_predictions.csv": {
                    "signature": signature,
                    "predictions": canonical,
                    "sent_at_utc": "2026-11-24T18:00:00+00:00",
                }
            },
            "pending": {},
        }
        self.state_path.write_text(json.dumps(legacy), encoding="utf-8")
        sender = Mock()
        result = self._process(self.rows, sender)
        self.assertEqual(result["status"], "UNCHANGED_SKIPPED")
        self.assertEqual(sender.call_count, 0)
        migrated = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["sources"]["todays_sample_predictions.csv"]["signature"], signature)
        self.assertNotIn("predictions", migrated["sources"]["todays_sample_predictions.csv"])
        self.assertNotIn("fixture-42", json.dumps(migrated))

    def test_partial_message_failure_retries_only_unsent_parts(self):
        from telegram_dedup import process_predictions

        messages = ["part-1", "part-2", "part-3"]
        builder = lambda rows, url, revision=False: messages
        attempts = []

        def fail_on_second(message, token, chat_id):
            attempts.append(message)
            if len(attempts) == 2:
                raise RuntimeError("temporary failure")

        with self.assertRaises(RuntimeError):
            process_predictions(
                self.rows, "token", "chat", "https://example.test/run",
                "todays_sample_predictions.csv", builder, fail_on_second,
                state_path=self.state_path, commit_to_git=False,
            )
        self.assertEqual(attempts, ["part-1", "part-2"])
        pending = json.loads(self.state_path.read_text(encoding="utf-8"))["pending"]
        self.assertEqual(pending["todays_sample_predictions.csv"]["sent_parts"], [0])

        retry_sender = Mock()
        result = process_predictions(
            self.rows, "token", "chat", "https://example.test/run",
            "todays_sample_predictions.csv", builder, retry_sender,
            state_path=self.state_path, commit_to_git=False,
        )
        self.assertEqual(result["status"], "FIRST_TABLE_SENT")
        self.assertEqual([call.args[0] for call in retry_sender.call_args_list], ["part-2", "part-3"])

    def test_new_signature_does_not_overwrite_incomplete_notification(self):
        from telegram_dedup import process_predictions

        messages = ["part-1", "part-2"]
        attempts = []

        def fail_on_second(message, token, chat_id):
            attempts.append(message)
            if message == "part-2":
                raise RuntimeError("temporary Telegram failure")

        with self.assertRaises(RuntimeError):
            process_predictions(
                self.rows, "token", "chat", "https://example.test/run",
                "todays_sample_predictions.csv",
                lambda rows, url, revision=False: messages,
                fail_on_second, state_path=self.state_path, commit_to_git=False,
            )
        before = json.loads(self.state_path.read_text(encoding="utf-8"))
        pending_before = before["pending"]["todays_sample_predictions.csv"]

        changed = [dict(
            self.rows[0], prediction_1x2="DRAW",
            prob_home="0.20", prob_draw="0.60", prob_away="0.20",
        )]
        retry_sender = Mock()
        with self.assertRaisesRegex(RuntimeError, "notification Telegram précédente est incomplète"):
            process_predictions(
                changed, "token", "chat", "https://example.test/run",
                "todays_sample_predictions.csv",
                lambda rows, url, revision=False: ["new-revision"],
                retry_sender, state_path=self.state_path, commit_to_git=False,
            )

        after = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            after["pending"]["todays_sample_predictions.csv"], pending_before
        )
        self.assertEqual(retry_sender.call_count, 0)
        self.assertEqual(attempts, ["part-1", "part-2"])

    def test_notification_workflows_share_serialized_concurrency_group(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        workflow_paths = (
            root / ".github" / "workflows" / "main.yml",
            root / ".github" / "workflows" / "pilot-validation.yml",
        )
        configurations = []
        for path in workflow_paths:
            content = path.read_text(encoding="utf-8").splitlines()
            group = next(line.split(":", 1)[1].strip().strip('"') for line in content
                         if line.startswith("  group:"))
            cancel = next(line.split(":", 1)[1].strip() for line in content
                          if line.startswith("  cancel-in-progress:"))
            configurations.append((group, cancel))

        self.assertEqual(configurations[0], configurations[1])
        self.assertEqual(configurations[0], ("betclic-prediction-pipeline", "false"))


if __name__ == "__main__":
    unittest.main()
