"""岗位底线引擎测试。"""
import unittest

from intake import ROOT, read_tables
from baseline import active_requirements, evaluate_pupil, solo_index, split_list


class RequirementWindowTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_left_closed_right_open(self):
        # r1: [1,9)；边界周 8 仍是 r1，周 9 切到 r2
        revisions = {r["revision"] for r in active_requirements(self.tables["requirements"], 8)}
        self.assertEqual(revisions, {"r1"})
        revisions = {r["revision"] for r in active_requirements(self.tables["requirements"], 9)}
        self.assertEqual(revisions, {"r2"})
        # r2 在 until 周（16）已经失效
        self.assertEqual(active_requirements(self.tables["requirements"], 16), [])

    def test_split_list(self):
        self.assertEqual(split_list("local,final"), ["local", "final"])
        self.assertEqual(split_list("-"), [])
        self.assertEqual(split_list(""), [])


class EvidenceCountingTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_assist_never_counts(self):
        cells, _, assists = solo_index(self.tables["evidence"], "mei", 6)
        # b3 是 final assist：不出现在独立作品集合，只出现在 assist 集合
        self.assertNotIn(("editor", "cuts", "final"), cells)
        self.assertIn("b3", assists[("editor", "cuts", "final")])

    def test_duplicate_artifact_counts_once_even_with_other_mentor(self):
        # v3(l3,nin) 被撤回前，v5 与 v3 共用 artifact l3 但导师是 shi
        cells, mentors, _ = solo_index(self.tables["evidence"], "lu", 6)
        key = ("ops", "cues", "final")
        self.assertEqual(cells[key], {"l3"})
        self.assertEqual(mentors[key], {"nin", "shi"})

    def test_distinct_artifacts_count_separately(self):
        cells, _, _ = solo_index(self.tables["evidence"], "mei", 5)
        self.assertEqual(cells[("editor", "cuts", "local")], {"b2"})
        self.assertEqual(cells[("editor", "cuts", "remote")], {"b1"})


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_r1_week8_verdicts(self):
        lu = evaluate_pupil(self.tables, "lu", 8, excluded_artifacts={"v3"})
        self.assertTrue(lu["roles"]["ops"])  # final 还有 v5
        mi = evaluate_pupil(self.tables, "mi", 8)
        self.assertFalse(mi["roles"]["ops"])
        self.assertIn(("ops", "cues", "final"),
                      {(d["role"], d["skill"], d["mode"]) for d in mi["deficiencies"]})
        mei = evaluate_pupil(self.tables, "mei", 8)
        self.assertTrue(mei["roles"]["editor"])

    def test_withdrawal_not_visible_before_receipt_week(self):
        # 第 7 周评估时第 8 周的撤回尚未发生
        before = evaluate_pupil(self.tables, "lu", 7)
        after = evaluate_pupil(self.tables, "lu", 8, excluded_artifacts={"v3"})
        # r1 下两种情形 ops 都达标，但 r2 下撤回会改变数量
        r2_after = evaluate_pupil(self.tables, "lu", 9, excluded_artifacts={"v3"})
        final_gap = next(d for d in r2_after["deficiencies"]
                         if d["skill"] == "cues" and d["mode"] == "final")
        self.assertEqual((final_gap["have"], final_gap["need"]), (1, 2))
        self.assertTrue(before["roles"]["ops"] and after["roles"]["ops"])

    def test_r2_requires_two_independent_per_mode(self):
        lu = evaluate_pupil(self.tables, "lu", 9, excluded_artifacts={"v3"})
        gaps = {(d["role"], d["skill"], d["mode"]) for d in lu["deficiencies"]}
        self.assertIn(("ops", "cues", "local"), gaps)
        self.assertIn(("ops", "cues", "final"), gaps)
        self.assertIn(("ops", "switch", "remote"), gaps)
        mei = evaluate_pupil(self.tables, "mei", 9)
        self.assertIn(("editor", "cuts", "final"),
                      {(d["role"], d["skill"], d["mode"]) for d in mei["deficiencies"]})

    def test_single_mentor_stream_flag(self):
        mi = evaluate_pupil(self.tables, "mi", 8)
        switch = next(s for s in mi["skills"] if s["skill"] == "switch")
        self.assertTrue(switch["single_mentor_stream"])
        lu = evaluate_pupil(self.tables, "lu", 8, excluded_artifacts={"v3"})
        cues = next(s for s in lu["skills"] if s["skill"] == "cues")
        self.assertFalse(cues["single_mentor_stream"])


if __name__ == "__main__":
    unittest.main()
