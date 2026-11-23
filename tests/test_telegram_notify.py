import unittest
from unittest.mock import Mock
from telegram_notify import build_message, send_telegram


class TelegramNotificationTests(unittest.TestCase):
    def test_message_contains_probabilities_and_no_bet(self):
        message = build_message([{
            "match_name": "Alpha - Beta",
            "prediction": "HOME",
            "prob_home": "0.61",
            "prob_draw": "0.24",
            "prob_away": "0.15",
            "action_status": "NO_BET_MODEL_UNVALIDATED",
            "over_2_5": "0.57",
            "btts_yes": "0.52",
        }], "https://github.com/example/run/1")
        self.assertIn("Alpha - Beta", message)
        self.assertIn("1 61.0%", message)
        self.assertIn("NO_BET_MODEL_UNVALIDATED", message)
        self.assertIn("rapport complet", message)
        self.assertIn("Over 2,5 : 57.0%", message)

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
