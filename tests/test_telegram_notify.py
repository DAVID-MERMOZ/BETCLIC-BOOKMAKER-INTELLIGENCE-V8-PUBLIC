import unittest
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


if __name__ == "__main__":
    unittest.main()
