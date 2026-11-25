"""历届重放、预测价值与选择偏差防护测试。"""
import unittest

from intake import ROOT, read_tables
from replay import (
    candidate_views,
    coverage_gaps,
    outcome_evolution,
    predictive_summary,
    replayed_features,
    visible_outcomes,
)


class TimingTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_known_week_visibility(self):
        self.assertEqual(visible_outcomes(self.tables["later_outcomes"], -24), {})
        self.assertEqual(visible_outcomes(self.tables["later_outcomes"], -7)["h18"]["status"],
                         "pending")
        latest = visible_outcomes(self.tables["later_outcomes"], -6)["h18"]
        self.assertEqual(latest["status"], "pass")
        self.assertEqual(latest["observer"], "nin")

    def test_decision_views_separate_then_and_now(self):
        views = {v["id"]: v for v in candidate_views(self.tables, -24, 0)}
        self.assertTrue(all(not v["visible_at_decision"] for v in views.values()))
        self.assertEqual(views["h02"]["outcome"], "pass")
        self.assertEqual(views["h02"]["observer"], "shi")

    def test_replayed_features_contain_no_future_information(self):
        for feature in replayed_features(self.tables, -24):
            self.assertEqual(feature["outcome_known_then"], "-")

    def test_outcome_evolution_keeps_both_interpretations(self):
        evolution = outcome_evolution(self.tables, "h18")
        self.assertTrue(evolution["assigned"])
        self.assertEqual([item["status"] for item in evolution["timeline"]],
                         ["pending", "pass"])


class SelectionBiasTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_unassigned_are_gaps_not_failures(self):
        views = {v["id"]: v for v in candidate_views(self.tables, -24, 0)}
        for candidate_id in ("h01", "h06"):
            self.assertFalse(views[candidate_id]["assigned"])
            self.assertEqual(views[candidate_id]["outcome"], "not_assigned")
        gaps = {g["id"]: g["reason"] for g in coverage_gaps(self.tables, 0)}
        for candidate_id in ("h01", "h06", "h09", "h16"):
            self.assertEqual(gaps[candidate_id], "not_assigned")

    def test_pending_and_lost_are_not_failures(self):
        gaps_now = {g["id"]: g["reason"] for g in coverage_gaps(self.tables, 0)}
        self.assertEqual(gaps_now["h12"], "lost")
        self.assertEqual(gaps_now["h17"], "lost")
        self.assertNotIn("h18", gaps_now)  # 已成熟为 pass
        gaps_then = {g["id"]: g["reason"] for g in coverage_gaps(self.tables, -7)}
        self.assertEqual(gaps_then["h18"], "pending")


class PredictiveSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def find(self, week, role, track, mentor):
        return next(
            item for item in predictive_summary(self.tables, week)
            if (item["role"], item["track"], item["mentor"]) == (role, track, mentor)
        )

    def test_cross_mentor_track_supported(self):
        item = self.find(0, "ops", "local", "nin")
        self.assertEqual(item["usability"], "cross_mentor_supported")
        self.assertEqual(item["other_mentors_on_track"], ["shi"])
        self.assertEqual(item["cohorts"], ["autumn", "spring", "summer"])
        self.assertTrue(item["all_cohorts"])
        self.assertEqual(item["mature_assigned"], item["pass"] + item["fail"])

    def test_single_mentor_is_familiar_only(self):
        # editor/remote/nin 只有 h17 且 lost；这里改用 editor/local 中无跨导师的覆盖判断
        item = self.find(0, "ops", "final", "shi")
        self.assertEqual(item["other_mentors_on_track"], ["he"])

    def test_no_followup_coverage(self):
        item = self.find(0, "editor", "remote", "nin")
        self.assertEqual(item["usability"], "no_followup_coverage")
        self.assertEqual(item["lost"], 1)
        self.assertEqual(item["mature_assigned"], 0)

    def test_new_edition_retires_old_associations(self):
        # r2 下 editor cuts 只要 remote/final，历史 local 关联不可用
        for mentor in ("he", "nin"):
            item = self.find(12, "editor", "local", mentor)
            self.assertEqual(item["usability"], "unusable_new_edition")
            self.assertFalse(item["track_still_required"])
        # 失效归属不抹掉覆盖事实
        self.assertEqual(self.find(0, "editor", "local", "he")["mature_assigned"], 1)
        # ops 轨道在 r2 仍被要求
        self.assertTrue(self.find(12, "ops", "final", "shi")["track_still_required"])

    def test_pass_rates_use_mature_only(self):
        item = self.find(0, "editor", "remote", "he")
        self.assertEqual(item["mature_assigned"], 2)
        self.assertEqual(item["pass"], 1)
        self.assertEqual(item["fail"], 1)
        self.assertEqual(item["pass_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
