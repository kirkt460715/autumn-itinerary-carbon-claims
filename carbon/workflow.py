"""按 as-of 时点编排整轮复核，产出可回查的工作流结果。

一轮复核的固定步骤（可重复、确定性）：
1. 加载并校验源表；
2. 按服务日与 as-of 钉版因子（共同参数，双方一致）；
3. 建立全量车段台账（含空驶追溯），做合乘份额与守恒核查；
4. 核算每个 offer 的每儿童区间与不确定来源；
5. 构建登记基准（共享段复用同一台账），跑同等服务门槛；
6. 应用截至 as_of 已送达的变更通知，形成标签判定；
7. 复核全部总卡/支线卡的在售窗口与独立贡献；
8. 汇总专题总数并保留覆盖范围与基准反查线索。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import accounting, benchmark, factors, review
from .accounting import OfferFootprint
from .benchmark import BenchmarkFootprint
from .model import Dataset
from .review import (
    AppliedNotice,
    CardReview,
    Comparison,
    LabelDecision,
    TopicTotal,
)


@dataclass
class OfferResult:
    offer_id: str
    service_day: date
    footprint: OfferFootprint
    benchmark_footprints: dict[str, BenchmarkFootprint]
    comparisons: list[Comparison]
    pin_description: dict[str, str]


@dataclass
class WorkflowResult:
    as_of: date
    data: Dataset
    notices: list[AppliedNotice]
    offers: dict[str, OfferResult]
    label_decisions: dict[str, LabelDecision]
    card_reviews: list[CardReview]
    topic_totals: list[TopicTotal]
    run_register: dict[str, accounting.LegEmission] = field(default_factory=dict)


def run(data: Dataset, as_of: date) -> WorkflowResult:
    notices = review.classify_notices(data, as_of)

    offer_results: dict[str, OfferResult] = {}
    # 每个服务日一个 FactorPin（因子按服务日适用）；同日所有 offer/基准共用
    pins_by_day: dict[date, factors.FactorPin] = {}
    registers_by_day: dict[date, dict[str, accounting.LegEmission]] = {}

    for offer in data.offers:
        service_day = data.leg(offer.legs[0]).day
        pin = pins_by_day.setdefault(
            service_day, factors.FactorPin(data, as_of, service_day)
        )
        register = registers_by_day.setdefault(
            service_day, accounting.build_run_register(data, pin)
        )
        footprint = accounting.offer_footprint(data, offer.offer, pin, register)

        bench_fps: dict[str, BenchmarkFootprint] = {}
        comparisons: list[Comparison] = []
        for bench in data.benchmarks:
            bench_fp = benchmark.benchmark_footprint(
                data, offer, bench, pin, register, as_of
            )
            bench_fps[bench.reference] = bench_fp
            comparisons.append(
                review.compare_offer_vs_benchmark(offer, footprint, bench_fp)
            )
        offer_results[offer.offer] = OfferResult(
            offer_id=offer.offer,
            service_day=service_day,
            footprint=footprint,
            benchmark_footprints=bench_fps,
            comparisons=comparisons,
            pin_description=pin.describe(),
        )

    decisions: dict[str, LabelDecision] = {}
    for label in data.labels:
        offer = data.offer(label.offer)
        result = offer_results[offer.offer]
        decisions[label.label] = review.decide_label(
            data,
            label,
            offer,
            result.footprint,
            result.comparisons,
            notices,
        )

    footprints = {oid: r.footprint for oid, r in offer_results.items()}
    card_reviews = review.review_cards(data, decisions, footprints, as_of)
    totals = review.topic_totals(data, card_reviews, decisions)

    primary_register = next(iter(registers_by_day.values()))
    return WorkflowResult(
        as_of=as_of,
        data=data,
        notices=notices,
        offers=offer_results,
        label_decisions=decisions,
        card_reviews=card_reviews,
        topic_totals=totals,
        run_register=primary_register,
    )


def rerun_after_cancellation(
    data: Dataset,
    as_of: date,
    remaining_offer_id: str,
    cancelled_tour: str,
) -> OfferFootprint:
    """取消后重算示例：剔除被取消团次的载客假设，剩余团队按实际份额重算。

    用于验证“一个团取消后，剩余团队结果不能沿用旧人均数”。被取消团的
    boardings 被排除后，合乘容量分母变化，剩余团的每儿童份额随之改变；
    空驶段通过 linked_leg 自动跟随关联载客段的剩余份额，无需手工调整。
    ``remaining_offer_id`` 是仍要出行的剩余团所对应的 offer。
    """
    offer = data.offer(remaining_offer_id)
    service_day = data.leg(offer.legs[0]).day
    pin = factors.FactorPin(data, as_of, service_day)

    route_legs = set(offer.legs)
    linked = {data.leg(l).linked_leg for l in offer.legs if data.leg(l).linked_leg}
    affected = route_legs | linked
    kept = [
        b
        for b in data.boardings
        if not (b.tour == cancelled_tour and b.leg in affected)
    ]
    reduced = Dataset(
        factors=data.factors,
        legs=data.legs,
        boardings=kept,
        offers=data.offers,
        benchmarks=data.benchmarks,
        changes=data.changes,
        labels=data.labels,
        cards=data.cards,
        _leg_index={l.leg: l for l in data.legs},
        _factor_index={(f.factor, f.version): f for f in data.factors},
    )
    register = accounting.build_run_register(reduced, pin)
    return accounting.offer_footprint(reduced, remaining_offer_id, pin, register)
