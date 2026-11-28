import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class PublicDataSafetyTests(unittest.TestCase):
    def test_main_workflow_uploads_only_aggregate_reports(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        artifact = workflow.split(
            "name: betclic-model-and-reports-", 1
        )[1].split("if-no-files-found", 1)[0]
        self.assertIn("reports/prediction_feedback_status.csv", artifact)
        self.assertIn("reports/prediction_history_evaluation.csv", artifact)
        self.assertNotIn("reports/*.csv", artifact)
        self.assertNotIn("artifacts/*.joblib", artifact)

    def test_pilot_workflow_does_not_publish_private_fixture_artifacts(self):
        workflow = (ROOT / ".github" / "workflows" / "pilot-validation.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("actions/upload-artifact@", workflow)
        self.assertNotIn("reports/todays_sample_predictions.csv", workflow)
        self.assertNotIn("artifacts/team_form_model.joblib", workflow)

    def test_human_registry_is_generated_locally_but_not_pushed_publicly(self):
        workflow = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        section = workflow.split(
            "name: Generer le registre de validation humaine localement (non publie)", 1
        )[1].split("name: Publier les rapports et le modele experimental", 1)[0]
        self.assertIn("python match_validation_registry.py", section)
        self.assertNotIn("git add", section)
        self.assertNotIn("git push", section)

    def test_public_telegram_state_has_no_fixture_rows(self):
        state = json.loads(
            (ROOT / "reports" / "telegram_prediction_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["version"], 2)
        for entry in state["sources"].values():
            self.assertEqual(set(entry), {"signature", "match_count", "sent_at_utc"})
            self.assertNotIn("predictions", entry)
        serialized = json.dumps(state)
        self.assertNotIn("match_id", serialized)
        self.assertNotIn("match_name", serialized)
        self.assertNotIn("prob_home", serialized)

    def test_database_diagnostic_does_not_dump_sqlite_rows(self):
        source = (ROOT / "database_diagnostic.py").read_text(encoding="utf-8")
        self.assertNotIn('print("  ", row)', source)
        self.assertNotIn("examples = conn.execute", source)
        self.assertIn("Détails des lignes d’exemple masqués", source)

    def test_odds_alert_logs_query_only_aggregate_types(self):
        from odds_analysis import show_recent_alerts

        class FakeConnection:
            query = ""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, query, params):
                self.query = query
                return self

            def fetchall(self):
                return [("ODDS_CHANGED",), ("SUSPENSION_CHANGED",)]

        connection = FakeConnection()
        output = StringIO()
        with patch("odds_analysis.get_connection", return_value=connection):
            with redirect_stdout(output):
                show_recent_alerts()
        self.assertIn("Alertes récentes (détails masqués) : 2", output.getvalue())
        self.assertNotIn("match_name", connection.query)
        self.assertNotIn("selection_name", connection.query)
        self.assertNotIn("Private Fixture", output.getvalue())


if __name__ == "__main__":
    unittest.main()
