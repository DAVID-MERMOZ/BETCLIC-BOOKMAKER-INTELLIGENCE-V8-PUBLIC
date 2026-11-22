import unittest
from match_results_report import norm, split_teams, similarity, parse_date, squad_profile, squad_compatible, date_similarity, diagnostic_reason, MAX_DATE_DELTA_HOURS


class MatchReportTests(unittest.TestCase):
    def test_team_normalization_handles_accents_and_club_suffixes(self):
        self.assertEqual(norm("São Paulo FC"), norm("Sao Paulo"))

    def test_team_normalization_preserves_meaningful_words(self):
        self.assertNotEqual(norm("The Athletic"), norm("Athletic"))
        self.assertNotEqual(norm("Deportivo Cali"), norm("Cali"))

    def test_exact_documented_team_aliases(self):
        aliases = [
            ("QPR", "Queens Park Rangers"),
            ("Naples", "SSC Napoli"),
            ("Kyoto", "Kyoto Sanga"),
            ("Western Sydney", "Western Sydney Wanderers FC"),
            ("Derby", "Derby County FC"),
            ("West Ham", "West Ham United FC"),
        ]
        for short_name, canonical_name in aliases:
            with self.subTest(short_name=short_name):
                self.assertEqual(norm(short_name), norm(canonical_name))
                self.assertEqual(similarity(short_name, canonical_name), 1.0)

    def test_additional_provider_short_names_normalize_exactly(self):
        aliases = [
            ("Stoke", "Stoke City FC"),
            ("Birmingham", "Birmingham City FC"),
            ("Wolverhampton", "Wolverhampton Wanderers FC"),
            ("Charlton", "Charlton Athletic FC"),
            ("Côme", "Como 1907"),
            ("Séville", "Sevilla FC"),
            ("Betis", "Real Betis Balompié"),
            ("UNAM Pumas", "Pumas UNAM"),
            ("Monterrey", "Rayados de Monterrey"),
            ("Bucheon", "Bucheon FC 1995"),
            ("Jeju United", "Jeju SK FC"),
        ]
        for short_name, canonical_name in aliases:
            with self.subTest(short_name=short_name):
                self.assertEqual(norm(short_name), norm(canonical_name))
                self.assertEqual(similarity(short_name, canonical_name), 1.0)

    def test_aliases_are_exact_not_partial(self):
        self.assertNotEqual(norm("QPR United"), norm("Queens Park Rangers"))
        self.assertNotEqual(norm("Naples United"), norm("SSC Napoli"))

    def test_team_normalization_handles_hyphens(self):
        self.assertEqual(norm("Al-Ain"), norm("Al Ain"))

    def test_split_teams_supports_common_delimiters(self):
        self.assertEqual(split_teams("Manchester City - Fulham"), ("Manchester City", "Fulham"))
        self.assertEqual(split_teams("Team A vs. Team B"), ("Team A", "Team B"))
        self.assertEqual(split_teams("Team A @ Team B"), ("Team A", "Team B"))

    def test_split_teams_does_not_split_unspaced_team_hyphen(self):
        self.assertEqual(split_teams("Al-Ain"), (None, None))

    def test_squad_profile_detects_women_youth_and_reserve(self):
        self.assertEqual(squad_profile("Arsenal Women"), {"women"})
        self.assertEqual(squad_profile("Barcelona U19"), {"youth"})
        self.assertEqual(squad_profile("Bayern II"), {"reserve"})

    def test_french_womens_marker_matches_provider_women_label(self):
        self.assertEqual(squad_profile("Arsenal F."), {"women"})
        self.assertEqual(squad_profile("Arsenal Women"), {"women"})
        self.assertTrue(squad_compatible("Arsenal F.", "Arsenal Women"))
        self.assertEqual(similarity("Arsenal F.", "Arsenal Women"), 1.0)

    def test_womens_marker_does_not_match_mens_first_team(self):
        self.assertFalse(squad_compatible("Arsenal F.", "Arsenal"))
        self.assertEqual(norm("Paris FC F."), norm("Paris FC Women"))

    def test_squad_compatibility_rejects_different_squads(self):
        self.assertTrue(squad_compatible("Arsenal", "Arsenal FC"))
        self.assertFalse(squad_compatible("Arsenal", "Arsenal Women"))
        self.assertFalse(squad_compatible("Bayern", "Bayern II"))
        self.assertFalse(squad_compatible("Ajax U21", "Ajax"))

    def test_similarity_rejects_empty_names(self):
        self.assertEqual(similarity("", "Fulham"), 0.0)

    def test_date_similarity_uses_actual_matching_window(self):
        self.assertEqual(date_similarity(0), 1.0)
        self.assertEqual(date_similarity(MAX_DATE_DELTA_HOURS), 0.0)
        self.assertEqual(date_similarity(MAX_DATE_DELTA_HOURS + 1), 0.0)

    def test_diagnostic_reason_distinguishes_all_cases(self):
        cases = [
            ((0, 0, 0, 0), "AUCUN_RESULTAT_FOURNISSEUR_DANS_12H"),
            ((4, 0, 0, 0), "PROFIL_EQUIPE_INCOMPATIBLE"),
            ((4, 3, 0, 0), "SIMILARITE_EQUIPE_INSUFFISANTE"),
            ((4, 3, 2, 0), "SCORE_CANDIDAT_SOUS_SEUIL"),
            ((4, 3, 2, 1), "CANDIDAT_PRESENT"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(diagnostic_reason(*args), expected)

    def test_parse_date_accepts_utc_and_rejects_invalid(self):
        self.assertIsNotNone(parse_date("2026-11-20T12:00:00Z"))
        self.assertIsNone(parse_date("not-a-date"))


if __name__ == "__main__":
    unittest.main()
