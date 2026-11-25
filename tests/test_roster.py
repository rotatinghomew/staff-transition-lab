"""席位校验、补证排班、退出处置与审计视图测试。"""
import unittest

from intake import ROOT, read_tables
from roster import (
    build_plan,
    handle_withdrawal,
    locked_assignments,
    mentor_covers,
    roster_audit,
    validate_slots,
    withdrawn_artifacts,
)


class SlotValidationTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_mentor_qualification_and_window(self):
        nin = next(m for m in self.tables["mentors"] if m["id"] == "nin")
        f2 = next(s for s in self.tables["slots"] if s["id"] == "f2")
        self.assertFalse(mentor_covers(nin, f2))  # nin 不覆盖 remote
        shi = next(m for m in self.tables["mentors"] if m["id"] == "shi")
        f5 = next(s for s in self.tables["slots"] if s["id"] == "f5")
        self.assertTrue(mentor_covers(shi, f5))

    def test_supervised_conflicts_but_dryrun_does_not(self):
        problems = validate_slots(self.tables)
        self.assertIn("mentor_not_qualified", problems["f2"])
        self.assertTrue(any("mentor_time_conflict" in p for p in problems["f1"]))
        self.assertTrue(any("mentor_time_conflict" in p for p in problems["f2"]))
        # f5(监督) 与 f6(演练) 同一导师时段不构成冲突
        self.assertEqual(problems["f5"], [])
        self.assertEqual(problems["f6"], [])


class LockAndWithdrawalTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_withdrawal_visible_only_from_receipt_week(self):
        self.assertEqual(withdrawn_artifacts(self.tables, as_of=7), set())
        self.assertEqual(withdrawn_artifacts(self.tables, as_of=8), {"v3"})

    def test_locked_assignments_parsed(self):
        locks = locked_assignments(self.tables)
        self.assertEqual([seat["pupil"] for seat in locks["f1"]], ["mi"])
        self.assertTrue(locks["f4"][0]["confirmed"])
        self.assertTrue(locks["f1"][0]["absent"])


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")
        self.plan = build_plan(self.tables, 9)

    def assignments(self, slot_id):
        return [a for a in self.plan["assignments"] if a["slot"] == slot_id]

    def test_locked_shifts_are_never_displaced(self):
        locked = {(a["slot"], a["pupil"]) for a in self.plan["assignments"]
                  if a["source"] == "receipt_locked"}
        self.assertEqual(locked, {("f1", "mi"), ("f3", "lu"), ("f4", "mei")})

    def test_independent_locked_slot_requires_baseline(self):
        # f3 是独立席，lu 在 week8 的 r1 下 ops 达标 → 无告警
        self.assertIsNone(self.assignments("f3")[0]["baseline_flag"])

    def test_remedial_seats_target_real_gaps_within_same_role(self):
        f7 = self.assignments("f7")
        self.assertEqual(len(f7), 1)
        self.assertEqual(f7[0]["pupil"], "mi")  # 缺 2 件，优先于 lu 的缺 1 件
        self.assertEqual(f7[0]["fills_deficiency"]["mode"], "final")
        # 没有任何 ops 观察的 mei 不会被塞进 ops 补证席
        self.assertNotIn("mei", [a["pupil"] for a in self.assignments("f7")])

    def test_same_mentor_stream_is_not_cross_validation(self):
        f5 = {a["pupil"]: a for a in self.assignments("f5")}
        self.assertEqual(set(f5), {"lu", "mi"})
        self.assertTrue(f5["lu"]["cross_validates"])   # lu 的 local 来自 nin，f5 是 shi
        self.assertFalse(f5["mi"]["cross_validates"])  # mi 的 local 本就是 shi

    def test_unqualified_and_dryrun_slots(self):
        # f2 资质不合格，不产生任何排班
        self.assertEqual(self.assignments("f2"), [])
        f6 = self.assignments("f6")
        # f6 与 f5 同一时段：lu/mi 已在补证席，演练席只能空出，不得叠加
        self.assertEqual(f6, [])

    def test_dryrun_books_when_time_block_is_free(self):
        tables = dict(self.tables)
        tables["slots"] = [dict(s) for s in self.tables["slots"]]
        # 删掉 f5 后，周 9 上午空出，f6 演练席可安排但不产生证据
        tables["slots"] = [s for s in tables["slots"] if s["id"] != "f5"]
        plan = build_plan(tables, 9)
        f6 = [a for a in plan["assignments"] if a["slot"] == "f6"]
        self.assertTrue(f6)
        self.assertTrue(all("演练" in a["reason"] for a in f6))
        self.assertTrue(all(a["fills_deficiency"] is None for a in f6))
        self.assertLessEqual(len(f6), 2)  # 容量上限

    def test_pupil_one_shift_per_time_block(self):
        seen = set()
        for a in self.plan["assignments"]:
            slot = next(s for s in self.tables["slots"] if s["id"] == a["slot"])
            key = (a["pupil"], slot["week"], slot["shift"])
            self.assertNotIn(key, seen)
            seen.add(key)


class WithdrawalDispositionTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")

    def test_must_rebook_without_qualified_replacement(self):
        result = handle_withdrawal(self.tables, "f1", 8)
        self.assertEqual(result["withdrawn_mentor"], "nin")
        self.assertEqual(result["replacement_mentors"], [])
        disposition = result["dispositions"][0]
        self.assertEqual(disposition["pupil"], "mi")
        self.assertEqual(disposition["category"], "must_rebook")
        # 达标的 lu 可顶独立履岗，但该观察不填补任何缺证
        alternatives = {a["pupil"]: a["category"] for a in result["alternative_pupils"]}
        self.assertEqual(alternatives.get("lu"), "replacement_duty_only")

    def test_rebook_with_qualified_replacement(self):
        tables = dict(self.tables)
        tables["mentors"] = [dict(m) for m in self.tables["mentors"]]
        tables["mentors"][1]["event_types"] = "local,remote,final"  # shi 增加 final 资质
        result = handle_withdrawal(tables, "f1", 8)
        disposition = result["dispositions"][0]
        self.assertEqual(disposition["category"], "rebook_with_replacement")
        self.assertEqual(disposition["replacement_mentor"], "shi")
        self.assertTrue(disposition["original_gap_still_open"])

    def test_independent_capable_when_baseline_met(self):
        # f3 是独立席；构造 nin 退出（独立席本无导师，这里直接验证 lu 的判定路径）
        tables = dict(self.tables)
        tables["slots"] = [dict(s) for s in self.tables["slots"]]
        target = next(s for s in tables["slots"] if s["id"] == "f3")
        target["mode"] = "supervised"
        target["mentor"] = "nin"
        result = handle_withdrawal(tables, "f3", 8)
        self.assertEqual(result["dispositions"][0]["category"], "independent_capable")


class AuditViewTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_tables(ROOT / "sample")
        self.audit = roster_audit(self.tables, 9, 9)

    def test_every_row_explains_reason_and_observation(self):
        for row in self.audit["rows"]:
            self.assertTrue(row["reason"])
            self.assertIn(row["observation_status"],
                          {"collected", "pending", "absent_pending_rebook", "not_formal"})
            self.assertTrue(row["carry_forward"])

    def test_observation_status_matches_receipts(self):
        by_slot = {r["slot"]: r for r in self.audit["rows"]}
        self.assertEqual(by_slot["f4"]["observation_status"], "collected")
        self.assertEqual(by_slot["f1"]["observation_status"], "absent_pending_rebook")

    def test_carry_forward_distinguishes_cross_and_familiar(self):
        rows = {(r["slot"], r["pupil"]): r for r in self.audit["rows"]}
        self.assertIn("cross_validated", rows[("f5", "lu")]["carry_forward"])
        self.assertIn("familiar_only", rows[("f5", "mi")]["carry_forward"])
        self.assertIn("not_gap", rows[("f4", "mei")]["carry_forward"])


if __name__ == "__main__":
    unittest.main()
