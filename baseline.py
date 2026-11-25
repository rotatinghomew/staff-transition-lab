"""岗位底线评估。

规则（与 README 业务约定一致）：

- 能力要求有效期左含右不含：``since <= week < until``。
- 只有 ``contribution=solo`` 才算独立观察；``assist`` 永远不计数。
- 同一个 ``artifact`` 重复签认（哪怕换了导师）只计一次，不扩大能力覆盖。
- ``must_modes`` 列出互异业务场景，每个场景都要达到 ``independent`` 件
  独立作品，该技能底线才算满足。

本模块只回答“个人在某周是否满足当时有效的岗位底线”，不使用任何历史
回访结论；预测层不得调用本模块的结论去替代底线。
"""
from __future__ import annotations


def split_list(text):
    """逗号分隔多值；``-`` 与空串表示不适用。"""
    if text in ("", "-"):
        return []
    return [part for part in text.split(",") if part]


def active_requirements(requirements, week):
    """返回 *week* 当周生效的要求行（左含右不含）。"""
    return [row for row in requirements if row["since"] <= week < row["until"]]


def solo_index(evidence, pupil, week):
    """汇总学员在 *week* 前已可见的观察。

    返回三项：

    ``cells``      : ``(role, skill, event_type) -> 独立作品 artifact 集合``
    ``mentors``    : 同一键 -> 这些作品的签认导师集合
    ``assists``    : 同一键 -> 只有 assist 记录、不能计独立观察的作品集合
    """
    cells: dict[tuple, set] = {}
    mentors: dict[tuple, set] = {}
    assists: dict[tuple, set] = {}
    for row in evidence:
        if row["pupil"] != pupil or row["week"] > week:
            continue
        key = (row["role"], row["skill"], row["event_type"])
        if row["contribution"] == "solo":
            cells.setdefault(key, set()).add(row["artifact"])
            mentors.setdefault(key, set()).add(row["mentor"])
        elif row["contribution"] == "assist":
            assists.setdefault(key, set()).add(row["artifact"])
    return cells, mentors, assists


def evaluate_pupil(tables, pupil, week, excluded_artifacts=None):
    """评估学员在 *week* 当周对所有生效要求的底线达成情况。

    ``excluded_artifacts`` 是调用方按评估时点判定的撤回作品集合
    （撤回收据的周次不得晚于 *week* 才应计入），默认不排除任何作品。
    """
    excluded_artifacts = set(excluded_artifacts or ())
    adjusted = dict(tables)
    if excluded_artifacts:
        adjusted["evidence"] = [
            row for row in tables["evidence"] if row["artifact"] not in excluded_artifacts
        ]
    cells, mentors, assists = solo_index(adjusted["evidence"], pupil, week)
    skills = []
    for req in active_requirements(tables["requirements"], week):
        mode_details = []
        mentor_pool: set[str] = set()
        for mode in split_list(req["must_modes"]):
            key = (req["role"], req["skill"], mode)
            have = len(cells.get(key, ()))
            mode_mentors = mentors.get(key, set())
            mentor_pool |= mode_mentors
            assist_only = sorted(assists.get(key, set()) - cells.get(key, set()))
            mode_details.append({
                "mode": mode,
                "have": have,
                "need": req["independent"],
                "met": have >= req["independent"],
                "mentors": sorted(mode_mentors),
                "assist_only_artifacts": assist_only,
            })
        skills.append({
            "revision": req["revision"],
            "role": req["role"],
            "skill": req["skill"],
            "met": all(detail["met"] for detail in mode_details),
            "single_mentor_stream": len(mentor_pool) <= 1 and bool(mentor_pool),
            "mentor_count": len(mentor_pool),
            "modes": mode_details,
        })

    roles: dict[str, bool] = {}
    for role in {item["role"] for item in skills}:
        roles[role] = all(item["met"] for item in skills if item["role"] == role)

    deficiencies = []
    for item in skills:
        for detail in item["modes"]:
            if not detail["met"]:
                deficiencies.append({
                    "role": item["role"],
                    "skill": item["skill"],
                    "mode": detail["mode"],
                    "have": detail["have"],
                    "need": detail["need"],
                })

    return {
        "pupil": pupil,
        "week": week,
        "roles": roles,
        "skills": skills,
        "deficiencies": deficiencies,
    }


def all_pupils(tables):
    """证据表与排班会签中出现过的全部学员。"""
    pupils = {row["pupil"] for row in tables["evidence"]}
    for receipt in tables["receipts"]:
        if receipt["action"] in {"assign", "confirmed"}:
            pupils.add(receipt["actor"])
    return sorted(pupils)
