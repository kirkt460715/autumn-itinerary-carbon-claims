"""把一轮复核结果导出为 CSV 与一份人工可读的 Markdown 复核报告。

所有输出都带可回查线索：因子版本、车段台账、份额依据、基准门槛原因、
通知状态、卡片覆盖键与专题覆盖范围。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .workflow import WorkflowResult


def _num(value):
    return "" if value is None else f"{value:.2f}"


def write_csvs(result: WorkflowResult, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # 1) 车段台账（运营排放与不确定来源）
    path = out_dir / "leg_register.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "leg",
                "vehicle_run",
                "basis",
                "mode",
                "km_lo",
                "km_hi",
                "factor",
                "rate_low",
                "rate_high",
                "emission_low",
                "emission_high",
                "linked_leg",
                "flags",
            ]
        )
        for leg_id, em in sorted(result.run_register.items()):
            writer.writerow(
                [
                    leg_id,
                    em.vehicle_run,
                    em.basis,
                    em.mode,
                    _num(em.kilometers_lo),
                    _num(em.kilometers_hi),
                    f"{em.factor_key} v{em.factor_version}"
                    if em.factor_key
                    else "",
                    _num(em.rate_low),
                    _num(em.rate_high),
                    _num(em.emission_low),
                    _num(em.emission_high),
                    em.linked_leg,
                    "|".join(em.flags),
                ]
            )
    written.append(path)

    # 2) 每团每段认领（份额依据，含空驶跟随）
    path = out_dir / "tour_claims.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "offer",
                "tour",
                "leg",
                "children",
                "capacity_units",
                "share",
                "claim_low",
                "claim_high",
                "flags",
            ]
        )
        for offer_id, res in sorted(result.offers.items()):
            for claim in res.footprint.tour_claims:
                writer.writerow(
                    [
                        offer_id,
                        claim.tour,
                        claim.leg,
                        claim.children,
                        claim.capacity_units,
                        f"{claim.share:.6f}",
                        _num(claim.claim_low),
                        _num(claim.claim_high),
                        "|".join(claim.flags),
                    ]
                )
    written.append(path)

    # 3) offer 每儿童区间
    path = out_dir / "offer_footprints.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "offer",
                "tour",
                "children",
                "per_child_low",
                "per_child_high",
                "blocking_flags",
                "assumption_flags",
                "factor_pin",
            ]
        )
        for offer_id, res in sorted(result.offers.items()):
            fp = res.footprint
            writer.writerow(
                [
                    offer_id,
                    fp.tour,
                    fp.children,
                    _num(fp.per_child_low),
                    _num(fp.per_child_high),
                    "|".join(fp.blocking_flags),
                    "|".join(fp.assumption_flags),
                    ";".join(f"{k}={v}" for k, v in sorted(res.pin_description.items())),
                ]
            )
    written.append(path)

    # 4) 基准比较与不采用原因
    path = out_dir / "comparisons.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "offer",
                "reference",
                "gate_passed",
                "gate_reasons",
                "offer_low",
                "offer_high",
                "benchmark_low",
                "benchmark_high",
                "worst_case_saving",
                "best_case_saving",
                "adopted",
                "not_adopted_reason",
            ]
        )
        for offer_id, res in sorted(result.offers.items()):
            for comp in res.comparisons:
                writer.writerow(
                    [
                        offer_id,
                        comp.reference,
                        comp.gate_passed,
                        "；".join(comp.gate_reasons),
                        _num(comp.offer_per_child_low),
                        _num(comp.offer_per_child_high),
                        _num(comp.benchmark_per_child_low),
                        _num(comp.benchmark_per_child_high),
                        _num(comp.worst_case_saving),
                        _num(comp.best_case_saving),
                        comp.adopted,
                        comp.not_adopted_reason,
                    ]
                )
    written.append(path)

    # 5) 标签判定
    path = out_dir / "label_decisions.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "label",
                "offer",
                "reference",
                "state",
                "eligible",
                "decision",
                "reasons",
                "blocking_notices",
                "settlement_note",
            ]
        )
        for label_id, dec in sorted(result.label_decisions.items()):
            writer.writerow(
                [
                    label_id,
                    dec.label.offer,
                    dec.chosen_reference,
                    dec.label.state,
                    dec.eligible,
                    dec.decision,
                    "；".join(dec.reasons),
                    "|".join(dec.blocking_notices),
                    dec.settlement_note,
                ]
            )
    written.append(path)

    # 6) 卡片复核
    path = out_dir / "card_reviews.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "card",
                "parent_card",
                "offer",
                "cohort",
                "on_sale_action",
                "window_covered",
                "independent_contribution",
                "coverage_key",
                "reasons",
            ]
        )
        for review in result.card_reviews:
            writer.writerow(
                [
                    review.card.card,
                    review.card.parent_card,
                    review.offer_id,
                    review.card.cohort,
                    review.action,
                    review.window_covered,
                    review.independent_contribution,
                    "|".join(review.coverage_key),
                    "；".join(review.reasons),
                ]
            )
    written.append(path)

    # 7) 专题合计
    path = out_dir / "topic_totals.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "cohort_scope",
                "included_cards",
                "excluded_cards",
                "total_worst_case_saving",
                "coverage_and_baselines",
            ]
        )
        for total in result.topic_totals:
            writer.writerow(
                [
                    total.cohort_scope,
                    "|".join(total.included_cards),
                    "|".join(f"{c}:{r}" for c, r in total.excluded_cards),
                    _num(total.total_worst_case_saving),
                    "|".join(
                        f"{e['card']}->{e['baseline']}@{'+'.join(e['coverage_key'])}"
                        for e in total.coverage_and_baselines
                    ),
                ]
            )
    written.append(path)

    # 8) 通知登记
    path = out_dir / "notice_register.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["notice", "offer", "action", "effective_at", "received_at", "status", "impacts"]
        )
        for applied in result.notices:
            ch = applied.notice
            writer.writerow(
                [
                    ch.notice,
                    ch.offer,
                    ch.action,
                    ch.effective_at.isoformat(),
                    ch.received_at.isoformat(),
                    applied.status,
                    "；".join(applied.impacts),
                ]
            )
    written.append(path)
    return written


def write_markdown(result: WorkflowResult, out_dir: Path) -> Path:
    lines: list[str] = []
    lines.append("# 低碳路线标签复核报告")
    lines.append("")
    lines.append(f"- 复核时点 as_of：{result.as_of.isoformat()}")
    lines.append("- 比较单位：一名参与研学活动的儿童及其必需交通份额（克 CO2e/儿童）")
    lines.append("- 保守减排口径：基准下界 − 本路线上界；达到标签下限才允许用标签")
    lines.append("")

    lines.append("## 1. 变更通知处理")
    for applied in result.notices:
        ch = applied.notice
        lines.append(
            f"- **{ch.notice}**（{ch.action}，offer {ch.offer}，"
            f"生效 {ch.effective_at}，送达 {ch.received_at}）→ {applied.status}："
            + "；".join(applied.impacts)
        )
    lines.append("")

    lines.append("## 2. 每儿童排放与不确定来源")
    for offer_id, res in sorted(result.offers.items()):
        fp = res.footprint
        rng = (
            f"{fp.per_child_low:.0f}–{fp.per_child_high:.0f}"
            if fp.per_child_low is not None
            else "无法定值"
        )
        lines.append(
            f"- **{offer_id}**（团 {fp.tour}，{fp.children} 名儿童）：{rng} 克/儿童"
        )
        if fp.blocking_flags:
            lines.append(f"  - 阻断性不确定：{'、'.join(fp.blocking_flags)}")
        if fp.assumption_flags:
            lines.append(f"  - 假设性标注（可展示范围）：{'、'.join(fp.assumption_flags)}")
        lines.append(f"  - 因子钉版：{res.pin_description}")
    lines.append("")

    lines.append("## 3. 基准同口径比较")
    for offer_id, res in sorted(result.offers.items()):
        lines.append(f"### {offer_id}")
        for comp in res.comparisons:
            if comp.adopted:
                lines.append(
                    f"- {comp.reference}：门槛通过；本路线 "
                    f"{comp.offer_per_child_low:.0f}–{comp.offer_per_child_high:.0f}，"
                    f"基准 {comp.benchmark_per_child_low:.0f}–"
                    f"{comp.benchmark_per_child_high:.0f}，"
                    f"保守减排 {comp.worst_case_saving:.0f} 克/儿童"
                )
            else:
                lines.append(
                    f"- {comp.reference}：**不采用**——{comp.not_adopted_reason}"
                )
    lines.append("")

    lines.append("## 4. 标签判定")
    for label_id, dec in sorted(result.label_decisions.items()):
        lines.append(
            f"- **{label_id}**（offer {dec.label.offer} 对 {dec.chosen_reference}，"
            f"原状态 {dec.label.state}）→ `{dec.decision}`"
        )
        for reason in dec.reasons:
            lines.append(f"  - {reason}")
        if dec.settlement_note:
            lines.append(f"  - 结算说明：{dec.settlement_note}")
    lines.append("")

    lines.append("## 5. 行程卡复核与专题合计")
    for review in result.card_reviews:
        lines.append(
            f"- {review.card.card}（{review.card.cohort}，父卡 "
            f"{review.card.parent_card or '—'}）：动作 `{review.action}`，"
            f"独立贡献={review.independent_contribution}"
        )
        for reason in review.reasons:
            lines.append(f"  - {reason}")
    lines.append("")
    for total in result.topic_totals:
        lines.append(
            f"- 专题 {total.cohort_scope}：计入 {list(total.included_cards) or '无'}，"
            f"保守合计 {total.total_worst_case_saving:.0f} 克/儿童"
        )
        for card, why in total.excluded_cards:
            lines.append(f"  - 剔除 {card}：{why}")
        for entry in total.coverage_and_baselines:
            lines.append(
                f"  - 反查 {entry['card']}：基准 {entry['baseline']}，"
                f"覆盖 {'/'.join(entry['coverage_key'])}"
            )
    lines.append("")

    path = out_dir / "review_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
