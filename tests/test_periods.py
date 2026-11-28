from __future__ import annotations

import unittest
from datetime import date

from sidjily.sidjilcom.criteria import criteria_from_mapping
from sidjily.sidjilcom.periods import PeriodPlan, split_window, with_period


class SplitWindowTests(unittest.TestCase):
    def test_one_year_windows_go_back_without_overlap(self) -> None:
        windows = split_window(date(2023, 1, 1), date(2026, 11, 27), 12)
        self.assertEqual(windows[0], (date(2025, 11, 28), date(2026, 11, 27)))
        self.assertEqual(windows[1], (date(2024, 11, 28), date(2025, 11, 27)))
        self.assertEqual(windows[-1][0], date(2023, 1, 1))
        for (lower, _), (_, next_upper) in zip(windows, windows[1:]):
            self.assertEqual((lower - next_upper).days, 1)

    def test_ten_years_from_1990_to_today(self) -> None:
        windows = split_window(date(1990, 1, 1), date(2026, 11, 27), 120)
        self.assertEqual(windows[0], (date(2016, 11, 28), date(2026, 11, 27)))
        self.assertEqual(windows[-1], (date(1990, 1, 1), date(1996, 11, 27)))
        self.assertEqual(len(windows), 4)

    def test_six_months_handles_month_ends(self) -> None:
        windows = split_window(date(2025, 1, 1), date(2025, 8, 31), 6)
        self.assertEqual(windows[0], (date(2025, 3, 1), date(2025, 8, 31)))
        self.assertEqual(windows[1], (date(2025, 1, 1), date(2025, 2, 28)))

    def test_period_is_added_to_criteria(self) -> None:
        criteria = criteria_from_mapping({"mode": "PERSONNE_MORALE", "activite": "442102"})
        dated = with_period(criteria, date(2025, 11, 28), date(2026, 11, 27))
        self.assertEqual(dated.common.date_inscription_du, "2025-11-28")
        self.assertEqual(dated.common.date_inscription_au, "2026-11-27")
        self.assertEqual(dated.common.activite, "442102")


class PeriodPlanTests(unittest.TestCase):
    def test_only_refused_windows_are_split_10_5_2_1_years_then_6_months(self) -> None:
        calls: list[tuple[date, date]] = []

        def run_window(lower: date, upper: date) -> tuple[str, int]:
            calls.append((lower, upper))
            span = (upper - lower).days
            # La décennie la plus récente reste trop grande jusqu'aux fenêtres de 6 mois.
            if upper.year >= 2017 and span > 200:
                return "too_large", 0
            return "collected", 5

        outcomes = PeriodPlan(date(1990, 1, 1), date(2026, 11, 27)).run(run_window)
        spans = [(upper - lower).days for lower, upper in calls]
        self.assertGreater(spans[0], 3000)            # 10 ans
        self.assertTrue(1700 < spans[1] < 1900)       # puis 5 ans
        self.assertTrue(700 < spans[2] < 760)         # 2 ans
        self.assertTrue(360 < spans[3] < 370)         # 1 an
        self.assertTrue(175 < spans[4] < 190)         # 6 mois
        self.assertIn("split", {outcome.status for outcome in outcomes})
        # Les décennies plus anciennes ne sont pas découpées.
        self.assertIn((date(2006, 11, 28), date(2016, 11, 27)), calls)
        collected = [outcome for outcome in outcomes if outcome.status == "collected"]
        self.assertTrue(all(outcome.rows == 5 for outcome in collected))

    def test_window_identical_to_a_refused_period_is_not_submitted_again(self) -> None:
        calls: list[tuple[date, date]] = []

        def run_window(lower: date, upper: date) -> tuple[str, int]:
            calls.append((lower, upper))
            return ("too_large", 0) if (upper - lower).days > 400 else ("collected", 3)

        start, end = date(2024, 11, 28), date(2026, 11, 27)
        outcomes = PeriodPlan(start, end).run(run_window, refused={(start, end)})
        # 2 ans < 10, 5 et 2 ans : la même période n'est pas resoumise; on passe directement à 1 an.
        self.assertEqual(calls, [(date(2025, 11, 28), end), (start, date(2025, 11, 27))])
        self.assertEqual([o.status for o in outcomes], ["collected", "collected"])

    def test_error_stops_the_plan_and_keeps_its_reason(self) -> None:
        outcomes = PeriodPlan(date(2020, 1, 1), date(2026, 11, 27)).run(
            lambda lower, upper: ("error", 0, "Session expirée")
        )
        self.assertEqual(len(outcomes), 1)
        self.assertIn("Session expirée", outcomes[0].describe())

    def test_incomplete_window_is_flagged_in_the_report(self) -> None:
        outcomes = PeriodPlan(date(2025, 1, 1), date(2026, 11, 27)).run(
            lambda lower, upper: ("collected", 10, "incomplète : 10/388 lues")
        )
        self.assertIn("incomplète : 10/388", outcomes[0].describe())

    def test_window_still_refused_at_six_months_is_reported_as_skipped(self) -> None:
        outcomes = PeriodPlan(date(2026, 1, 1), date(2026, 11, 27)).run(lambda lower, upper: ("too_large", 0))
        self.assertTrue(any(outcome.status == "skipped" for outcome in outcomes))
        self.assertIn("ignorée", [o for o in outcomes if o.status == "skipped"][0].describe())


if __name__ == "__main__":
    unittest.main()
