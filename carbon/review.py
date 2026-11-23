"""标签判定、变更通知影响、卡片复核与专题合计。

判定口径（与业务叙述逐条对应）：

- 只在“同一可用服务口径”下比较：基准未通过同口径门槛即不构成减排证据；
- 减排差距用保守口径：worst_case_saving = 基准下界 − 本路线上界（每儿童克），
  只有 worst_case 仍 >= 标签 minimum_saving 才允许使用标签；
- 存在阻断性不确定来源（交通方式未确认/因子缺失/里程待核对/已生效取消或
  换车）时不得使用标签，只能展示标明假设的排放范围；
- 共享车次或共同因子通过 FactorPin 与车段台账复用，禁止各取有利未知值；
- 已兑现行程保留原声明，另附结算复核说明；
- 取消/换车后剩余团队按实际服务重算，不沿用旧人均数；
- 支线卡引用总卡同一批孩子的实际交通时，不产生独立贡献，专题合计不得
  把两张卡的估计减排各算一份；另售不同组且证据独立时才计入独立贡献。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .accounting import OfferFootprint
from .benchmark import BenchmarkFootprint
from .model import Card, Change, Dataset, Label, Offer

BLOCKING_NOTICE_ACTIONS = {"cancelled", "vehicle_unconfirmed", "actual_detour"}
# actual_detour 发生在已兑现行程上：保留原声明，但阻断对该卡新发放/续用标签
HARD_UNCERTAINTY = {"mode_unconfirmed", "factor_missing", "distance_pending"}


@dataclass
class AppliedNotice:
    notice: Change
    status: str  # applied | pending_future_receipt
    impacts: tuple[str, ...]


@dataclass
class Comparison:
    reference: str
    gate_passed: bool
    gate_reasons: tuple[str, ...]
    offer_per_child_low: float | None
    offer_per_child_high: float | None
    benchmark_per_child_low: float | None
    benchmark_per_child_high: float | None
    worst_case_saving: float | None
    best_case_saving: float | None
    adopted: bool
    not_adopted_reason: str


@dataclass
class LabelDecision:
    label: Label
    chosen_reference: str
    comparisons: list[Comparison]
    eligible: bool
    decision: str  # retain_with_settlement | withdraw | deny | approve
    reasons: tuple[str, ...]
    blocking_notices: tuple[str, ...]
    settlement_note: str


@dataclass
class CardReview:
    card: Card
    offer_id: str
    label_decision: LabelDecision | None
    window_covered: bool
    action: str  # retain | withdraw_label | no_label | not_on_sale
    independent_contribution: bool
    coverage_key: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass
class TopicTotal:
    cohort_scope: str
    included_cards: tuple[str, ...]
    excluded_cards: tuple[tuple[str, str], ...]
    independent_savings: list[dict[str, object]]
    total_worst_case_saving: float | None
    coverage_and_baselines: list[dict[str, object]]


def classify_notices(data: Dataset, as_of: date) -> list[AppliedNotice]:
    applied: list[AppliedNotice] = []
    for change in data.changes:
        if change.received_at > as_of:
            applied.append(
                AppliedNotice(
                    change,
                    "pending_future_receipt",
                    ("通知尚未送达，本轮不改变在售口径，登记为待处理",),
                )
            )
            continue
        if change.action == "cancelled":
            impacts = ("团次取消：剩余团队按实际服务重算，旧人均数作废",)
        elif change.action == "vehicle_unconfirmed":
            impacts = ("换车后交通方式未确认：阻断标签直至方式与因子补齐",)
        elif change.action == "actual_detour":
            impacts = ("实际换乘/绕行偏离计划：里程待核对，触发结算复核",)
        else:
            impacts = (f"未识别动作 {change.action}，按阻断处理",)
        applied.append(AppliedNotice(change, "applied", impacts))
    return applied


def notices_for_offer(
    applied_notices: list[AppliedNotice], offer_id: str
) -> list[AppliedNotice]:
    return [a for a in applied_notices if a.notice.offer == offer_id]


def compare_offer_vs_benchmark(
    offer: Offer,
    footprint: OfferFootprint,
    benchmark_fp: BenchmarkFootprint,
) -> Comparison:
    gate = benchmark_fp.gate
    worst = best = None
    adopted = False
    reason = ""
    if not gate.passed:
        reason = "未通过同等服务口径门槛：" + "；".join(gate.reasons)
    elif footprint.per_child_low is None or benchmark_fp.per_child_low is None:
        reason = "存在阻断性不确定来源，无法形成数值比较"
    else:
        worst = benchmark_fp.per_child_low - footprint.per_child_high
        best = benchmark_fp.per_child_high - footprint.per_child_low
        adopted = True
    return Comparison(
        reference=benchmark_fp.reference,
        gate_passed=gate.passed,
        gate_reasons=gate.reasons,
        offer_per_child_low=footprint.per_child_low,
        offer_per_child_high=footprint.per_child_high,
        benchmark_per_child_low=benchmark_fp.per_child_low,
        benchmark_per_child_high=benchmark_fp.per_child_high,
        worst_case_saving=worst,
        best_case_saving=best,
        adopted=adopted,
        not_adopted_reason=reason,
    )


def decide_label(
    data: Dataset,
    label: Label,
    offer: Offer,
    footprint: OfferFootprint,
    comparisons: list[Comparison],
    applied_notices: list[AppliedNotice],
) -> LabelDecision:
    reasons: list[str] = []
    blocking_notice_ids = tuple(
        a.notice.notice
        for a in applied_notices
        if a.notice.offer == offer.offer
        and a.notice.action in BLOCKING_NOTICE_ACTIONS
        and a.status == "applied"
    )
    hard_uncertainty = tuple(
        f for f in footprint.blocking_flags if f in HARD_UNCERTAINTY
    )

    target = next((c for c in comparisons if c.reference == label.reference), None)
    chosen_reference = label.reference

    # 对每个其他候选基准留下确切条件与不采用原因（可比较但不自动替换登记基准）
    for comp in comparisons:
        if comp.reference == label.reference:
            continue
        if not comp.gate_passed:
            reasons.append(
                f"候选基准 {comp.reference} 不采用：{comp.not_adopted_reason}"
            )
        elif comp.adopted:
            threshold_alt = comp.worst_case_saving >= label.minimum_saving
            condition = (
                f"保守减排 {comp.worst_case_saving:.0f} 克/儿童"
                + ("达到" if threshold_alt else "未达到")
                + f"标签下限 {label.minimum_saving:.0f}"
            )
            reasons.append(
                f"候选基准 {comp.reference} 通过同口径门槛（{condition}），"
                f"但标签 {label.label} 登记基准为 {label.reference}，"
                "不自动替换；改用须重新登记并留痕"
            )

    # 登记基准若不可用，检查是否存在其他可用候选，但必须留下不采用/改用原因
    if target is None or not target.adopted:
        alt = next(
            (c for c in comparisons if c.adopted and c.worst_case_saving is not None),
            None,
        )
        if alt is not None:
            reasons.append(
                f"登记基准 {label.reference} 不可用；候选 {alt.reference} 可比较，"
                "但标签证据须与登记基准一致，故不自动替换，需重新登记"
            )
        elif target is not None:
            reasons.append(target.not_adopted_reason)

    eligible = False
    threshold_met = False
    if target is not None and target.adopted:
        threshold_met = target.worst_case_saving >= label.minimum_saving
        if not threshold_met:
            reasons.append(
                f"保守减排 {target.worst_case_saving:.0f} 克/儿童 "
                f"< 标签下限 {label.minimum_saving:.0f}，证据不足"
            )
    if hard_uncertainty:
        reasons.append("阻断性不确定来源：" + "、".join(hard_uncertainty))
    if blocking_notice_ids:
        reasons.append("已生效变更通知：" + "、".join(blocking_notice_ids))

    eligible = bool(
        target is not None
        and target.adopted
        and threshold_met
        and not hard_uncertainty
        and not blocking_notice_ids
    )

    # 决策与既有标签状态结合
    settlement_note = ""
    if label.state == "used":
        # 已兑现行程：原声明保留，附当前证据下的结算复核说明
        decision = "retain_with_settlement"
        if target is not None and target.adopted and not threshold_met:
            settlement_note = (
                f"行程已兑现，保留原 {label.label} 声明；按当前因子与实际载客复核，"
                f"保守差距 {target.worst_case_saving:.0f} 克/儿童低于原下限 "
                f"{label.minimum_saving:.0f}，该声明不得用于新售卖窗口"
            )
        elif blocking_notice_ids or hard_uncertainty:
            settlement_note = (
                f"行程已兑现，保留原 {label.label} 声明；结算存在待核对项："
                + "、".join(reasons)
            )
        else:
            settlement_note = (
                f"行程已兑现，保留原 {label.label} 声明；结算复核通过"
            )
    elif label.state == "active":
        decision = "approve" if eligible else "withdraw"
        if not eligible:
            reasons.append("在售标签不再满足条件，应予撤下")
    else:  # requested
        decision = "approve" if eligible else "deny"
        if not eligible:
            reasons.append("标签申请证据不足，不予发放（可展示标明假设的排放范围）")

    return LabelDecision(
        label=label,
        chosen_reference=chosen_reference,
        comparisons=comparisons,
        eligible=eligible,
        decision=decision,
        reasons=tuple(reasons),
        blocking_notices=blocking_notice_ids,
        settlement_note=settlement_note,
    )


def review_cards(
    data: Dataset,
    decisions: dict[str, LabelDecision],
    footprints: dict[str, OfferFootprint],
    as_of: date,
) -> list[CardReview]:
    reviews: list[CardReview] = []
    # 覆盖证据键：同一 offer+tour+路线车段集合视为同一批孩子的同一交通证据
    coverage: dict[str, tuple[str, ...]] = {
        o.offer: (o.tour, *o.legs) for o in data.offers
    }
    parent_by_id = {c.card: c for c in data.cards}

    for card in data.cards:
        offer = data.offer(card.offer)
        on_sale = card.active_from <= as_of <= card.active_until
        label = next((l for l in data.labels if l.offer == card.offer), None)
        decision = decisions.get(label.label) if label else None

        window_covered = True
        reasons: list[str] = []
        if decision is not None:
            lo, hi = decision.label.valid_window
            if not (lo <= card.active_from and card.active_until <= hi):
                window_covered = False
                reasons.append("卡片售卖窗口超出标签 valid_window，标签不适用该窗口")

        # 独立贡献判定（依据“是不是同一批孩子”，而非仅看父子关系）：
        # - 支线卡与父卡引用同一 tour 的实际交通 → 同一批孩子，不另计贡献；
        # - 支线卡另售给不同 tour（即便共享同一服务车次）→ 不同组且有共同
        #   证据，独立贡献成立，但各自按本团份额认领，车次总量不翻倍。
        independent = True
        if card.parent_card:
            parent = parent_by_id[card.parent_card]
            same_children = coverage[card.offer][0] == coverage[parent.offer][0]
            if same_children:
                independent = False
                reasons.append(
                    f"支线卡 {card.card} 引用总卡 {parent.card} 同一批孩子"
                    f"（{coverage[card.offer][0]}）的实际交通，"
                    "不另计独立减排，专题合计不得重复"
                )
            else:
                reasons.append(
                    f"支线卡 {card.card} 另售给不同组"
                    f"（{coverage[card.offer][0]}，父卡为 {coverage[parent.offer][0]}），"
                    "共享车次按各团份额分别认领，独立贡献成立"
                )

        if not on_sale:
            if decision is not None and decision.decision == "retain_with_settlement":
                # 已兑现行程：不在当前在售窗口，但原声明保留并附结算说明
                action = "retain"
                reasons.append(
                    f"as_of={as_of} 已超出在售窗口，行程已兑现，保留原声明与结算说明"
                )
            else:
                action = "not_on_sale"
                reasons.append(f"as_of={as_of} 不在卡片在售窗口内")
        elif decision is None:
            action = "no_label"
        elif decision.decision in ("withdraw", "deny") or not window_covered:
            action = "withdraw_label"
        elif decision.decision == "retain_with_settlement":
            action = "retain"
        else:
            action = "retain"

        reviews.append(
            CardReview(
                card=card,
                offer_id=card.offer,
                label_decision=decision,
                window_covered=window_covered,
                action=action,
                independent_contribution=independent,
                coverage_key=coverage[offer.offer],
                reasons=tuple(reasons),
            )
        )
    return reviews


def topic_totals(
    data: Dataset,
    reviews: list[CardReview],
    decisions: dict[str, LabelDecision],
) -> list[TopicTotal]:
    """按专题（cohort 范围）合计，只计独立且获批的贡献，并保留可反查覆盖。"""
    scopes = sorted(
        {card.cohort.removesuffix("-extra") for card in data.cards}
    )
    totals: list[TopicTotal] = []
    for scope in scopes:
        scope_cards = [
            r
            for r in reviews
            if r.card.cohort == scope or r.card.cohort == f"{scope}-extra"
        ]
        included: list[str] = []
        excluded: list[tuple[str, str]] = []
        savings: list[dict[str, object]] = []
        coverage_log: list[dict[str, object]] = []
        for card_review in scope_cards:
            card = card_review.card
            decision = card_review.label_decision
            if not card_review.independent_contribution:
                excluded.append((card.card, "与总卡同一批孩子同一交通证据，覆盖不独立"))
                continue
            if decision is None or decision.decision not in ("approve",):
                excluded.append(
                    (
                        card.card,
                        "标签未获批/已撤下/仅保留历史声明，不形成可合计减排",
                    )
                )
                continue
            target = next(
                c
                for c in decision.comparisons
                if c.reference == decision.chosen_reference
            )
            included.append(card.card)
            savings.append(
                {
                    "card": card.card,
                    "offer": card_review.offer_id,
                    "reference": decision.chosen_reference,
                    "worst_case_saving_per_child": target.worst_case_saving,
                }
            )
            coverage_log.append(
                {
                    "card": card.card,
                    "coverage_key": card_review.coverage_key,
                    "baseline": decision.chosen_reference,
                }
            )
        total_worst = (
            sum(item["worst_case_saving_per_child"] for item in savings)
            if savings
            else 0.0
        )
        totals.append(
            TopicTotal(
                cohort_scope=scope,
                included_cards=tuple(included),
                excluded_cards=tuple(excluded),
                independent_savings=savings,
                total_worst_case_saving=total_worst,
                coverage_and_baselines=coverage_log,
            )
        )
    return totals
