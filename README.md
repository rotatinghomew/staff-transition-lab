# 转岗技能证据与实习机会的联动

实习记录是场次、职责、实际监督和作品归属事实，不等于资格判断。`contribution=assist` 不是独立履岗。`event_type` 用于判断覆盖是否跨互异业务场景；同一个 `artifact` 重复签认，不增加观察次数。能力要求有效期左含右不含，`must_modes` 为不同场景标识，`independent` 为每种场景最少独立观察数。`supervised` 席位占用一个导师时段，且需要场内具备相应资格；`dryrun` 席位不计正式值岗。`past_candidates` 记录当届决策日已知的带教计数和实际出场选择；`later_outcomes` 的 `pass`、`fail` 是两周内独立值岗经作业制片签认的结果，`pending` 未到成熟期，`lost` 是已到期但没收回记录。观察结果的 `observer` 是实际现场审核人，不一定是带教导师；`known_week` 之前不可见。无出场机会的人没有独立结果。春、夏、秋三个历史批次在同一相对周历中依次编号；分组中同一导师的学员共享带教观察条件。资历仅为合成角色，不含真实个人信息。

## 评估纪律与代码落点

| 纪律 | 实现位置 |
| --- | --- |
| 只有 solo 计独立观察，assist 单列但永不计数 | `baseline.solo_index` |
| 同一 artifact 重复签认（含换导师）只计一次 | `baseline.solo_index` 按作品去重 |
| 要求左含右不含，按评估周选版本 | `baseline.active_requirements` |
| 逐场景核对最少独立数，输出个人已满足/未满足的岗位底线 | `baseline.evaluate_pupil` |
| 撤回在收据周及之后才可见，不追溯改变更早结论 | `roster.withdrawn_artifacts` |
| 决策日重放只用当时已知的归属、资历与计数 | `replay.replayed_features`、`replay.candidate_views` |
| 无出场者记无随访覆盖，不记不胜任；pending/lost 不是失败 | `replay.coverage_gaps`、`replay.predictive_summary` |
| 预测只排序补证机会，不参与岗位底线判定 | `baseline` 不导入 `replay`；`report.py replay` 附 guardrails |
| 新版要求下失效的旧关联单独标记，保留其历史覆盖事实 | `replay.predictive_summary` 的 `unusable_new_edition` |
| 单一导师随访只支持熟悉场景内继续带教 | `familiar_setting_only`、排班的 `same_mentor_stream` 标记 |
| 锁定班次与已确认补证席不被观察收益挤占 | `roster.build_plan` 先落锁后排新席 |
| 补证席只选能改变底线判断、且属本人岗位轨迹的机会 | `roster.build_plan` 第一轮 |
| 同一导师的多个机会不算互证；跨导师才置 `cross_validates` | `roster._cell_mentors` |
| 导师退出三分类：独立值岗 / 改签 / 替代履岗但补不到缺证 | `roster.handle_withdrawal` |
| 班次可查安排理由、观察收齐状态与能否结转下一季 | `roster.roster_audit` |
| 当初证据解释与后来校验结果分开保存 | `replay.outcome_evolution`、`candidate_views.visible_at_decision` |

## 模块

- `intake.py`：读取 CSV，只检查表头与标量格式，不判断业务状态。
- `baseline.py`：岗位底线引擎。按周选生效要求版本，汇总 solo 作品（去重、去撤回），逐技能逐场景给出达成情况与缺证单元。
- `replay.py`：历届时点重放。维护回访可见时点、成熟度与无覆盖人群，按 岗位×场景×导师 汇总预测价值，并区分跨导师支持、仅熟悉场景、无随访覆盖、新版失效四类可用性。
- `roster.py`：席位资质与冲突校验、锁定班次保护、补证监督席与独立/演练席排班、导师退出处置、主管审计视图。
- `report.py`：命令行主管视图。

## 数据包

`sample/` 是本地合成的业务记录，均为 UTF-8 CSV；`table-format.json` 给出各表的列顺序与标量类型，`str` 中 `-` 表示不适用，逗号分隔多值。不同表中同名业务标识用于关联，具体含义见上文。

## 运行

使用 Python 3.11 标准库，不需要安装三方依赖。

```sh
python3 intake.py
python3 -m unittest discover -s tests -v
```

主管视图（资料目录作为可选位置参数，默认 `sample/`，放在子命令之后）：

```sh
# 个人/全员在某周的岗位底线结论
python3 report.py baseline --week 9
python3 report.py baseline --week 8 --pupil lu

# 春届决策日重放，并用当前可见回访校验预测价值
python3 report.py replay --decision-week -24 --current-week 0

# 补证排班与班次审计（安排理由、观察状态、结转结论）
python3 report.py roster --planning-week 9 --current-week 9

# 导师临时退出时逐人判定处置类别
python3 report.py withdraw f1 --week 8

# 单个候选人回访结论的时间演变（当初解释 vs 后来校验）
python3 report.py evolution h18
```

也可以将同格式的资料目录作为位置参数，例如：

```sh
python3 report.py /path/to/other-dataset roster --planning-week 9 --current-week 9
```
