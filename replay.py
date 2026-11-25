"""历届数据的时点重放与带教证据的预测价值校验。

核心纪律：

1. **只看决策日已知信息**：重放某届候选人时，只用 ``past_candidates``
   里当时记录的带教计数、归属（导师）、资历（profile）和出场选择，
   不用之后才回访到的结果。
2. **选择偏差不反推不胜任**：``assigned=0`` 的人没有独立值岗结果，
   一律不进入 pass/fail 统计，只能算“无独立随访覆盖”。
3. **回访有时点**：``known_week`` 之后才可见；``pending`` 未成熟、
   ``lost`` 到期未回收，二者都不是失败。
4. **预测只服务于补证机会的选择**：本模块的结论是补证优先级输入，
   不能替代 ``baseline`` 算出的岗位底线。
5. **新版要求隔离**：旧版本要求下形成的关联，如果对应的场景在新要求
   里已不是必备模式，就标记为当前版本不可用。
6. **同导师不算互证**：全部观察来自同一位导师时，只支持在熟悉场景内
   继续带教，不能当作跨导师可复现的证据。
"""
from __future__ import annotations

from baseline import active_requirements, split_list

PENDING = "pending"
LOST = "lost"
PASS = "pass"
FAIL = "fail"
NO_OUTCOME = "no_outcome"
NOT_ASSIGNED = "not_assigned"


def visible_outcomes(later_outcomes, week):
    """*week* 当周可见的最新回访（同一 id 多条时以后出现的记录为准）。"""
    latest: dict[str, dict] = {}
    for row in later_outcomes:
        if row["known_week"] > week:
            continue
        latest[row["id"]] = row
    return dict(latest)


def candidate_views(tables, decision_week, current_week):
    """重放各候选人在 *decision_week* 决策日的快照。

    每条记录附加当时不可见、在 *current_week* 才可见的回访状态，便于
    区分“当初采用的证据解释”和“后来校验的结果”。
    """
    now_visible = visible_outcomes(tables["later_outcomes"], current_week)
    then_visible = visible_outcomes(tables["later_outcomes"], decision_week)
    views = []
    for row in tables["past_candidates"]:
        if row["week"] != decision_week:
            continue
        later = now_visible.get(row["id"])
        if not row["assigned"]:
            outcome = NOT_ASSIGNED
        elif later is None:
            outcome = NO_OUTCOME
        else:
            outcome = later["status"]
        views.append({
            "id": row["id"],
            "cohort": row["cohort"],
            "role": row["role"],
            "edition": row["edition"],
            "track": row["track"],
            "mentor": row["mentor"],
            "profile": row["profile"],
            "assisted": row["assisted"],
            "solo": row["solo"],
            "assigned": row["assigned"],
            "outcome": outcome,
            "observer": later["observer"] if later else "-",
            "known_week": later["known_week"] if later else None,
            "visible_at_decision": row["id"] in then_visible,
        })
    return views


def outcome_evolution(tables, candidate_id):
    """单个候选人回访结论随时间的演变，保留“当初解释/后来校验”的区分。"""
    rows = [r for r in tables["later_outcomes"] if r["id"] == candidate_id]
    timeline = []
    for index, row in enumerate(rows, start=1):
        timeline.append({
            "record_order": index,
            "known_week": row["known_week"],
            "status": row["status"],
            "observer": row["observer"],
        })
    assigned = any(r["id"] == candidate_id and r["assigned"] for r in tables["past_candidates"])
    return {"id": candidate_id, "assigned": assigned, "timeline": timeline}


def _current_modes(tables, role, week):
    """当前评估口径下该岗位仍然要求的场景集合。

    口径选择：优先取 *week* 当周生效的要求；若当周处于版本之间的空档
    （历史周历中尚无要求生效），锚定最新一版要求作为“当前版本”，避免
    把空档期误判为全部历史关联失效。
    """
    rows = active_requirements(tables["requirements"], week)
    if not rows:
        latest_revision = max(r["revision"] for r in tables["requirements"])
        rows = [r for r in tables["requirements"] if r["revision"] == latest_revision]
    rows = [row for row in rows if row["role"] == role]
    modes: set[str] = set()
    revisions = set()
    for row in rows:
        modes.update(split_list(row["must_modes"]))
        revisions.add(row["revision"])
    return modes, revisions


def predictive_summary(tables, current_week):
    """按 岗位×场景×导师 汇总带教观察对后续独立履岗的预测价值。

    只统计实际出场（``assigned=1``）且已成熟（pass/fail）的候选人；
    pending/lost 计入未成熟/未回收，无出场者计入未覆盖。
    """
    cohorts = sorted({r["cohort"] for r in tables["past_candidates"]})
    groups: dict[tuple, dict] = {}
    for row in tables["past_candidates"]:
        key = (row["role"], row["track"], row["mentor"])
        bucket = groups.setdefault(key, {
            "role": row["role"],
            "track": row["track"],
            "mentor": row["mentor"],
            "editions": set(),
            "cohorts": set(),
            "assigned": 0,
            "pass": 0,
            "fail": 0,
            "pending": 0,
            "lost": 0,
            "not_assigned": 0,
            "decision_features": [],
        })
        bucket["editions"].add(row["edition"])
        bucket["cohorts"].add(row["cohort"])
        bucket["decision_features"].append({
            "id": row["id"],
            "cohort": row["cohort"],
            "profile": row["profile"],
            "assisted": row["assisted"],
            "solo": row["solo"],
            "assigned": row["assigned"],
        })
        later = visible_outcomes(tables["later_outcomes"], current_week).get(row["id"])
        if not row["assigned"]:
            bucket["not_assigned"] += 1
        elif later is None:
            continue
        elif later["status"] in (PASS, FAIL):
            bucket["assigned"] += 1
            bucket[later["status"]] += 1
        elif later["status"] == PENDING:
            bucket["pending"] += 1
        elif later["status"] == LOST:
            bucket["lost"] += 1

    summary = []
    for bucket in groups.values():
        modes, revisions = _current_modes(tables, bucket["role"], current_week)
        mature = bucket["pass"] + bucket["fail"]
        track_still_required = bucket["track"] in modes
        mentors_on_track = sorted({
            key[2] for key in groups
            if key[0] == bucket["role"] and key[1] == bucket["track"]
        })
        cross_mentor = len(mentors_on_track) >= 2
        # 失效判断放在最后：即使新版不再要求，仍保留该组历史上的覆盖事实。
        if mature == 0:
            usability = "no_followup_coverage"
            reason = "没有成熟的独立随访结果可校验"
        elif cross_mentor:
            usability = "cross_mentor_supported"
            reason = "同场景在互异导师下均有成熟随访，可用于跨场景补证排序"
        else:
            usability = "familiar_setting_only"
            reason = "只有单一导师的随访覆盖，仅支持熟悉场景内继续带教"
        if not track_still_required:
            reason = "该场景在当前版本要求中已不是必备模式；" + reason
            usability = "unusable_new_edition"
        summary.append({
            "role": bucket["role"],
            "track": bucket["track"],
            "mentor": bucket["mentor"],
            "editions": sorted(bucket["editions"]),
            "cohorts": sorted(bucket["cohorts"]),
            "all_cohorts": bucket["cohorts"] == set(cohorts),
            "mature_assigned": mature,
            "pass": bucket["pass"],
            "fail": bucket["fail"],
            "pass_rate": (bucket["pass"] / mature) if mature else None,
            "pending": bucket["pending"],
            "lost": bucket["lost"],
            "not_assigned": bucket["not_assigned"],
            "other_mentors_on_track": [m for m in mentors_on_track if m != bucket["mentor"]],
            "current_revisions": sorted(revisions),
            "track_still_required": track_still_required,
            "usability": usability,
            "reason": reason,
            "decision_features": bucket["decision_features"],
        })
    summary.sort(key=lambda item: (item["role"], item["track"], item["mentor"]))
    return summary


def coverage_gaps(tables, current_week):
    """无独立随访覆盖的人群：没拿到出场机会或观察未到期/未回收。"""
    visible = visible_outcomes(tables["later_outcomes"], current_week)
    gaps = []
    for row in tables["past_candidates"]:
        later = visible.get(row["id"])
        if not row["assigned"]:
            kind = NOT_ASSIGNED
        elif later is None:
            kind = NO_OUTCOME
        elif later["status"] in (PENDING, LOST):
            kind = later["status"]
        else:
            continue
        gaps.append({
            "id": row["id"],
            "cohort": row["cohort"],
            "role": row["role"],
            "track": row["track"],
            "mentor": row["mentor"],
            "reason": kind,
        })
    return gaps


def replayed_features(tables, decision_week):
    """严格按决策日已知信息重放的特征表（不含任何未来信息）。

    用于和 :func:`predictive_summary` 的事后校验结果对照，确认指标选择
    没有被未来信息污染。
    """
    then = visible_outcomes(tables["later_outcomes"], decision_week)
    rows = []
    for row in tables["past_candidates"]:
        if row["week"] != decision_week:
            continue
        rows.append({
            "id": row["id"],
            "mentor": row["mentor"],
            "profile": row["profile"],
            "assisted": row["assisted"],
            "solo": row["solo"],
            "assigned": row["assigned"],
            "outcome_known_then": then.get(row["id"], {}).get("status", "-"),
        })
    return rows
