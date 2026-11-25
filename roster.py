"""实习席位校验、补证排班与导师退出处置。

席位语义（见 README）：

- ``supervised``：占用一个导师时段，场内导师须具备该岗位与场景资质；
  完成并收齐观察后可产出计入岗位底线的独立作品。
- ``independent``：正式独立值岗，不占用导师时段；只派给已满足岗位
  底线的人，不能作为缺证者的补证手段。
- ``dryrun``：演练席，不计正式值岗、不产出底线证据。

排班纪律：

1. 已派班/已确认的席位是**锁定班次**，观察收益再高也不得挤占；
2. 补证席只选能改变底线判断的机会，即席位的岗位/技能/场景对应该学员
   尚未满足的底线单元；底线按**席位所在周**生效的要求版本判断；
3. 若可选机会的导师与该学员既有独立观察的导师完全相同，标记为
   ``same_mentor_stream``：新作品仍计入底线数量，但不是可互证的多份
   证据；
4. 同一学员同一时段只能占一个席位，同一导师同一时段只能带一个席位；
5. 导师临时退出时分三类处置：仍可独立值岗、必须改签、可在替代现场
   履岗但补不到原先所缺证据。
"""
from __future__ import annotations

from baseline import evaluate_pupil, split_list

SUPERVISED = "supervised"
INDEPENDENT = "independent"
DRYRUN = "dryrun"


def withdrawn_artifacts(tables, as_of=None):
    """收据中在 *as_of* 周（含）以前被撤回的证据/作品标识。

    撤回是发生在具体周次的事件：评估更早周次的状态时，未来的撤回
    还不可见，不能追溯生效。
    """
    evidence_ids = {row["id"] for row in tables["evidence"]}
    return {
        receipt["ref"] for receipt in tables["receipts"]
        if receipt["action"] == "withdraw"
        and receipt["ref"] in evidence_ids
        and (as_of is None or receipt["week"] <= as_of)
    }


def _adjusted_tables(tables, excluded):
    if not excluded:
        return tables
    adjusted = dict(tables)
    adjusted["evidence"] = [
        row for row in tables["evidence"] if row["artifact"] not in excluded
    ]
    return adjusted


def _verdict(tables, pupil, week):
    # 撤回只在撤回周及之后可见，因此按被评估周次过滤收据。
    excluded = withdrawn_artifacts(tables, as_of=week)
    return evaluate_pupil(tables, pupil, week, excluded_artifacts=excluded)


def mentor_covers(mentor, slot):
    """导师资质是否覆盖席位的岗位与场景，且在有效期内。"""
    if mentor["role"] != slot["role"]:
        return False
    if not (mentor["since"] <= slot["week"] < mentor["until"]):
        return False
    return slot["event_type"] in split_list(mentor["event_types"])


def qualified_mentors(tables, slot):
    return [m["id"] for m in tables["mentors"] if mentor_covers(m, slot)]


def locked_assignments(tables):
    """收据派班：slot_id -> [{pupil, seq, confirmed, absent}]。"""
    locked: dict[str, list[dict]] = {}
    absent_slots = {
        receipt["ref"] for receipt in tables["receipts"] if receipt["action"] == "absent"
    }
    confirmed_slots = {
        receipt["ref"] for receipt in tables["receipts"] if receipt["action"] == "confirmed"
    }
    for receipt in tables["receipts"]:
        if receipt["action"] != "assign":
            continue
        locked.setdefault(receipt["ref"], []).append({
            "pupil": receipt["actor"],
            "seq": receipt["seq"],
            "confirmed": receipt["ref"] in confirmed_slots,
            "absent": receipt["ref"] in absent_slots,
        })
    return locked


def validate_slots(tables):
    """逐席位校验资质、导师时段冲突与容量。

    只有正式监督席占用导师时段；dryrun 是演练席，不产生时段冲突。
    """
    occupancy: dict[tuple, list[str]] = {}
    result = {}
    for slot in tables["slots"]:
        problems = []
        if slot["mode"] in (SUPERVISED, DRYRUN) and slot["mentor"] != "-":
            if slot["mentor"] not in qualified_mentors(tables, slot):
                problems.append("mentor_not_qualified")
            if slot["mode"] == SUPERVISED:
                key = (slot["week"], slot["shift"], slot["mentor"])
                occupancy.setdefault(key, []).append(slot["id"])
        if slot["mode"] == INDEPENDENT and slot["mentor"] != "-":
            problems.append("independent_slot_should_have_no_mentor")
        result[slot["id"]] = problems
    for (week, shift, mentor), slot_ids in occupancy.items():
        if len(slot_ids) > 1:
            for slot_id in slot_ids:
                result[slot_id].append(
                    f"mentor_time_conflict:{mentor}:w{week}{shift}:{'|'.join(slot_ids)}"
                )
    locks = locked_assignments(tables)
    for slot in tables["slots"]:
        taken = len(locks.get(slot["id"], []))
        if taken > slot["capacity"]:
            result[slot["id"]].append(f"capacity_exceeded:{taken}>{slot['capacity']}")
    return result


def _cell_mentors(tables, pupil, role, skill, mode, week):
    """截至 *week* 仍有效的独立观察签认导师集合（未来撤回不可见）。"""
    excluded = withdrawn_artifacts(tables, as_of=week)
    mentors = set()
    for row in tables["evidence"]:
        if (row["pupil"] == pupil and row["role"] == role and row["skill"] == skill
                and row["event_type"] == mode and row["contribution"] == "solo"
                and row["artifact"] not in excluded and row["week"] <= week):
            mentors.add(row["mentor"])
    return mentors


def _gap_map(verdict):
    return {
        (d["role"], d["skill"], d["mode"]): d for d in verdict["deficiencies"]
    }


def _role_membership(tables):
    """每个学员实际进入过带教记录的岗位集合。

    补证只能补本人已有岗位轨迹上的缺证单元：没有任何该岗位观察的人，
    不能靠一个监督席跨岗位取得资格。
    """
    membership: dict[str, set] = {}
    for row in tables["evidence"]:
        membership.setdefault(row["pupil"], set()).add(row["role"])
    return membership


def build_plan(tables, planning_week):
    """生成排班方案：锁定班次 + 补证监督席 + 独立值岗/演练安排。"""
    problems = validate_slots(tables)
    locks = locked_assignments(tables)
    slot_by_id = {slot["id"]: slot for slot in tables["slots"]}
    membership = _role_membership(tables)

    pupils = sorted({row["pupil"] for row in tables["evidence"]}
                    | {seat["pupil"] for seats in locks.values() for seat in seats})
    verdict_cache: dict[tuple, dict] = {}

    def verdict(pupil, week):
        key = (pupil, week)
        if key not in verdict_cache:
            verdict_cache[key] = _verdict(tables, pupil, week)
        return verdict_cache[key]

    assignments = []
    pupil_busy: dict[tuple, set] = {}
    slot_taken: dict[str, int] = {slot_id: len(seats) for slot_id, seats in locks.items()}

    for slot_id, seats in sorted(locks.items()):
        slot = slot_by_id[slot_id]
        for seat in seats:
            pupil_busy.setdefault((seat["pupil"], slot["week"], slot["shift"]), set()).add(slot_id)
            baseline_flag = None
            if slot["mode"] == INDEPENDENT:
                role_met = verdict(seat["pupil"], slot["week"])["roles"].get(slot["role"], False)
                if not role_met:
                    baseline_flag = "baseline_unmet_locked"
            assignments.append({
                "slot": slot_id,
                "pupil": seat["pupil"],
                "source": "receipt_locked",
                "confirmed": seat["confirmed"],
                "absent": seat["absent"],
                "reason": "已派班锁定，不得被观察收益挤占",
                "fills_deficiency": None,
                "cross_validates": None,
                "baseline_flag": baseline_flag,
            })

    def book(slot, pupil, reason, deficiency, cross):
        slot_taken[slot["id"]] = slot_taken.get(slot["id"], 0) + 1
        pupil_busy.setdefault((pupil, slot["week"], slot["shift"]), set()).add(slot["id"])
        assignments.append({
            "slot": slot["id"],
            "pupil": pupil,
            "source": "planned",
            "confirmed": False,
            "absent": False,
            "reason": reason,
            "fills_deficiency": deficiency,
            "cross_validates": cross,
            "baseline_flag": None,
        })

    # 第一轮：补证监督席。只选能改变底线判断、且席位本身可用的机会。
    for slot in sorted(tables["slots"], key=lambda s: s["id"]):
        if slot["mode"] != SUPERVISED or problems[slot["id"]]:
            continue
        while slot_taken.get(slot["id"], 0) < slot["capacity"]:
            candidates = []
            for pupil in pupils:
                if slot["role"] not in membership.get(pupil, set()):
                    continue
                # 同一时段一人一席，无论席位模式。
                if pupil_busy.get((pupil, slot["week"], slot["shift"])):
                    continue
                gaps = _gap_map(verdict(pupil, slot["week"]))
                gap = gaps.get((slot["role"], slot["skill"], slot["event_type"]))
                if gap is None:
                    continue
                stream = _cell_mentors(
                    tables, pupil, slot["role"], slot["skill"], slot["event_type"], slot["week"]
                )
                cross = bool(stream - {slot["mentor"]})
                # 缺得越深越优先；跨导师流加分；同分按学号确定性排序。
                score = (gap["need"] - gap["have"]) * 100 + (50 if cross else 0)
                candidates.append((score, pupil, gap, cross))
            if not candidates:
                break
            candidates.sort(key=lambda item: (-item[0], item[1]))
            _, pupil, gap, cross = candidates[0]
            book(
                slot, pupil,
                "补证席：完成并收齐观察后可把该底线单元从缺证改为满足"
                + ("；导师与既有观察互异，可交叉验证" if cross
                   else "；导师与既有独立观察同流，不构成可互证的多份证据"),
                {"role": gap["role"], "skill": gap["skill"], "mode": gap["mode"],
                 "have": gap["have"], "need": gap["need"]},
                cross,
            )

    # 第二轮：独立值岗席只派给岗位底线已满足的人；演练席标注不产生证据。
    for slot in sorted(tables["slots"], key=lambda s: s["id"]):
        if problems[slot["id"]]:
            continue
        while slot_taken.get(slot["id"], 0) < slot["capacity"]:
            candidates = []
            for pupil in pupils:
                if slot["role"] not in membership.get(pupil, set()):
                    continue
                if slot["id"] in pupil_busy.get((pupil, slot["week"], slot["shift"]), set()):
                    continue
                # 同一时段已被其他席位占用（监督席/独立席/演练席均算）。
                if pupil_busy.get((pupil, slot["week"], slot["shift"])):
                    continue
                role_met = verdict(pupil, slot["week"])["roles"].get(slot["role"], False)
                if slot["mode"] == INDEPENDENT:
                    if role_met:
                        candidates.append((pupil, "正式独立值岗：岗位底线已满足，不用于补证"))
                elif slot["mode"] == DRYRUN:
                    candidates.append((pupil, "演练席：不计正式值岗，不产出底线证据"))
            if not candidates:
                break
            pupil, reason = sorted(candidates, key=lambda item: item[0])[0]
            book(slot, pupil, reason, None, None)

    return {
        "planning_week": planning_week,
        "excluded_artifacts": sorted(withdrawn_artifacts(tables)),
        "slot_problems": problems,
        "verdicts": {
            pupil: verdict(pupil, week)
            for (pupil, week) in sorted(verdict_cache)
        },
        "assignments": assignments,
        "slot_taken": slot_taken,
    }


def handle_withdrawal(tables, slot_id, week):
    """导师在 *week* 临时退出时，对席位相关人员逐类判定。"""
    slot = next(s for s in tables["slots"] if s["id"] == slot_id)
    locks = locked_assignments(tables)
    seated = [seat["pupil"] for seat in locks.get(slot_id, [])]
    # qualified_mentors 已保证场景资质覆盖，这里只排除退出者本人。
    replacements = [m for m in qualified_mentors(tables, slot) if m != slot["mentor"]]

    dispositions = []
    for pupil in seated:
        v = _verdict(tables, pupil, week)
        role_met = v["roles"].get(slot["role"], False)
        target_gap = (slot["role"], slot["skill"], slot["event_type"]) in _gap_map(v)
        if role_met:
            dispositions.append({
                "pupil": pupil,
                "category": "independent_capable",
                "detail": "岗位底线已满足，无导师在场仍可独立值岗",
                "replacement_mentor": None,
                "original_gap_still_open": False,
            })
        elif not replacements:
            dispositions.append({
                "pupil": pupil,
                "category": "must_rebook",
                "detail": "无具备资质的替代导师，席位必须改签，缺证保持开放",
                "replacement_mentor": None,
                "original_gap_still_open": target_gap,
            })
        elif target_gap:
            dispositions.append({
                "pupil": pupil,
                "category": "rebook_with_replacement",
                "detail": f"改由具备资质的替代导师 {replacements[0]} 签认，原缺证可补",
                "replacement_mentor": replacements[0],
                "original_gap_still_open": True,
            })
        else:
            dispositions.append({
                "pupil": pupil,
                "category": "replacement_duty_only",
                "detail": "可在替代现场履岗，但该席位场景不是本人缺证单元，补不到原先所缺证据",
                "replacement_mentor": replacements[0],
                "original_gap_still_open": True,
            })

    alternatives = []
    for pupil in sorted({r["pupil"] for r in tables["evidence"]}):
        if pupil in seated:
            continue
        v = _verdict(tables, pupil, week)
        if v["roles"].get(slot["role"], False):
            alternatives.append({
                "pupil": pupil,
                "category": "replacement_duty_only",
                "detail": "可在替代现场独立履岗；该学员此岗位已达标，观察不填补任何缺证",
            })

    return {
        "slot": slot_id,
        "week": week,
        "withdrawn_mentor": slot["mentor"],
        "replacement_mentors": replacements,
        "dispositions": dispositions,
        "alternative_pupils": alternatives,
    }


def roster_audit(tables, planning_week, current_week):
    """主管视角的班次审计行：安排理由、观察是否收齐、能否结转下一季。"""
    plan = build_plan(tables, planning_week)
    rows = []
    for slot in tables["slots"]:
        for assignment in [a for a in plan["assignments"] if a["slot"] == slot["id"]]:
            if assignment["confirmed"]:
                observation = "collected"
            elif assignment["absent"]:
                observation = "absent_pending_rebook"
            elif slot["mode"] == DRYRUN:
                observation = "not_formal"
            else:
                observation = "pending"
            if slot["mode"] == SUPERVISED and assignment["fills_deficiency"]:
                if assignment["cross_validates"]:
                    carry = "observed_then_cross_validated: 可作为跨导师证据结转下一季"
                else:
                    carry = "observed_then_familiar_only: 仅支持熟悉场景内继续带教"
            elif slot["mode"] == SUPERVISED:
                carry = "observed_then_not_gap: 观察可归档，但不改变任何岗位底线结论"
            elif slot["mode"] == INDEPENDENT:
                carry = "independent_duty: 成熟回访后进入历届独立随访"
            else:
                carry = "not_carried: 不产生可结转证据"
            rows.append({
                "slot": slot["id"],
                "week": slot["week"],
                "shift": slot["shift"],
                "role": slot["role"],
                "skill": slot["skill"],
                "mode": slot["mode"],
                "event_type": slot["event_type"],
                "mentor": slot["mentor"],
                "pupil": assignment["pupil"],
                "source": assignment["source"],
                "reason": assignment["reason"],
                "fills_deficiency": assignment["fills_deficiency"],
                "cross_validates": assignment["cross_validates"],
                "baseline_flag": assignment["baseline_flag"],
                "slot_problems": plan["slot_problems"][slot["id"]],
                "observation_status": observation,
                "carry_forward": carry,
                "visible_now": current_week >= planning_week,
            })
    return {"planning_week": planning_week, "current_week": current_week, "rows": rows, "plan": plan}
