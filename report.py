"""主管视图：底线评估、历届重放、排班审计与退出处置的统一入口。

示例：
    python3 report.py baseline --week 9
    python3 report.py replay --decision-week -24 --current-week 0
    python3 report.py roster --planning-week 9 --current-week 9
    python3 report.py withdraw f1 --week 8
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from intake import ROOT, read_tables
from baseline import all_pupils, evaluate_pupil
from replay import (
    candidate_views,
    coverage_gaps,
    outcome_evolution,
    predictive_summary,
    replayed_features,
)
from roster import handle_withdrawal, roster_audit


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def command_baseline(tables, args):
    pupils = [args.pupil] if args.pupil else all_pupils(tables)
    emit({
        "as_of_week": args.week,
        "note": "结论仅依据 solo 独立作品；assist 与同作品重复签认不扩大覆盖",
        "pupils": [evaluate_pupil(tables, pupil, args.week) for pupil in pupils],
    })


def command_replay(tables, args):
    emit({
        "decision_week": args.decision_week,
        "current_week": args.current_week,
        "replayed_at_decision": replayed_features(tables, args.decision_week),
        "candidate_views": candidate_views(
            tables, args.decision_week, args.current_week
        ),
        "predictive_summary": predictive_summary(tables, args.current_week),
        "coverage_gaps": coverage_gaps(tables, args.current_week),
        "guardrails": [
            "预测只用于选择补证机会，不替代岗位底线",
            "assigned=0 者无独立结果，记为无随访覆盖而非不胜任",
            "pending/lost 不是失败；观察未到期不进入 pass/fail 统计",
            "unusable_new_edition 的旧关联不得用于新版要求下的排序",
            "familiar_setting_only 只支持熟悉场景内继续带教",
        ],
    })


def command_roster(tables, args):
    emit(roster_audit(tables, args.planning_week, args.current_week))


def command_withdraw(tables, args):
    emit(handle_withdrawal(tables, args.slot, args.week))


def command_evolution(tables, args):
    emit(outcome_evolution(tables, args.candidate))


def build_parser():
    parser = argparse.ArgumentParser(description="转岗技能证据与实习机会联动评估")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_directory(p):
        p.add_argument("directory", type=Path, nargs="?", default=ROOT / "sample",
                       help="资料目录，默认 sample/")

    baseline = sub.add_parser("baseline", help="按生效要求评估岗位底线")
    add_directory(baseline)
    baseline.add_argument("--week", type=int, required=True)
    baseline.add_argument("--pupil", type=str)
    baseline.set_defaults(func=command_baseline)

    replay = sub.add_parser("replay", help="历届决策时点重放与预测价值校验")
    add_directory(replay)
    replay.add_argument("--decision-week", type=int, required=True)
    replay.add_argument("--current-week", type=int, required=True)
    replay.set_defaults(func=command_replay)

    roster = sub.add_parser("roster", help="补证排班与班次审计")
    add_directory(roster)
    roster.add_argument("--planning-week", type=int, required=True)
    roster.add_argument("--current-week", type=int, required=True)
    roster.set_defaults(func=command_roster)

    withdraw = sub.add_parser("withdraw", help="导师临时退出处置")
    add_directory(withdraw)
    withdraw.add_argument("slot", type=str)
    withdraw.add_argument("--week", type=int, required=True)
    withdraw.set_defaults(func=command_withdraw)

    evolution = sub.add_parser("evolution", help="单个候选人回访结论的时间演变")
    add_directory(evolution)
    evolution.add_argument("candidate", type=str)
    evolution.set_defaults(func=command_evolution)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        tables = read_tables(args.directory)
    except (OSError, ValueError) as error:
        parser.exit(2, f"资料读取失败：{error}\n")
    args.func(tables, args)


if __name__ == "__main__":
    main()
