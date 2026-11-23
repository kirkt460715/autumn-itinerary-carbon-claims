"""车段与每儿童排放核算。

共同认领依据（公开可解释，同一服务车次对所有团一致）：

1. 车段运营排放 = 服务车公里 × 适用因子（克 CO2e/车公里），里程与因子
   各自给出上下界，排放区间下界 = km_lo × rate_low，上界 = km_hi × rate_high。
2. 合乘份额按“占用容量单位”划分：每个团在该段的容量单位 =
   儿童 + 陪同成人 + 轮椅位（轮椅位是必须保留的专用容量，占用车辆能力，
   故计入分母而不是只按人头）。份额 = 本团容量单位 / 同段全部团容量单位。
   这样需要轮椅位的小团不会被“坐满车的人均量”错误比较。
3. 空驶段（linked_leg 非空）不产生新的载客归属，其排放按所关联载客段
   各团的同一份额认领——即“接回同一批乘客所需的空驶”跟着该批乘客走。
4. 守恒校验：每个车段各团认领之和必须等于该车段的运营排放（含空驶段），
   保证同一服务车次的排放在分拆团次后既不消失也不翻倍。
5. 每儿童份额 = 本团在 offer 全部车段的认领总量 / 本团儿童数。比较单位
   固定为“一名参与研学活动的儿童及其必需的交通份额”。

不确定来源逐段登记、互不混淆：
- mode_unconfirmed 交通方式尚无法确认（因子无法正式匹配）
- factor_missing 正式因子表无适用发档
- distance_pending 里程待核对（上界缺失）
- boarding_estimated 载客数为订购时假设（estimated），非实际确认
- plan_basis 车段本身是计划版本而非实跑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .factors import FactorPin
from .model import Boarding, Dataset, Leg

EPSILON = 1e-6


@dataclass(frozen=True)
class LegEmission:
    leg: str
    vehicle_run: str
    mode: str
    basis: str
    kilometers_lo: float | None
    kilometers_hi: float | None
    factor_key: str | None
    factor_version: int | None
    rate_low: float | None
    rate_high: float | None
    emission_low: float | None
    emission_high: float | None
    linked_leg: str
    flags: tuple[str, ...]
    selection_note: str


@dataclass
class TourClaim:
    leg: str
    tour: str
    children: int
    capacity_units: int
    share: float
    claim_low: float | None
    claim_high: float | None
    flags: tuple[str, ...]


@dataclass
class OfferFootprint:
    offer: str
    tour: str
    legs: tuple[str, ...]
    children: int
    per_child_low: float | None
    per_child_high: float | None
    leg_flags: dict[str, tuple[str, ...]]
    blocking_flags: tuple[str, ...]
    assumption_flags: tuple[str, ...]
    tour_claims: list[TourClaim] = field(default_factory=list)
    conservation_checked: bool = True


def leg_flags(leg: Leg, boardings: list[Boarding], factor_available: bool) -> tuple[str, ...]:
    flags: list[str] = []
    if leg.mode == "unconfirmed":
        flags.append("mode_unconfirmed")
    if not factor_available:
        flags.append("factor_missing")
    if leg.kilometers_lo is None or leg.kilometers_hi is None:
        flags.append("distance_pending")
    if any(b.basis == "estimated" for b in boardings):
        flags.append("boarding_estimated")
    if leg.basis == "plan":
        flags.append("plan_basis")
    return tuple(flags)


def compute_leg_emission(
    data: Dataset, leg: Leg, pin: FactorPin
) -> LegEmission:
    boardings = data.boardings_on(leg.leg)
    selected = pin.select(leg.region, leg.mode)
    flags = leg_flags(leg, boardings, selected is not None)
    if selected is None or leg.kilometers_lo is None:
        low = high = None
        rate_low = rate_high = None
        factor_key = factor_version = None
        note = "无适用正式因子或里程缺失，未产生数值核算"
    else:
        factor = selected.factor
        factor_key, factor_version = factor.factor, factor.version
        rate_low, rate_high = factor.rate_low, factor.rate_high
        low = leg.kilometers_lo * rate_low
        high = (
            leg.kilometers_hi * rate_high
            if leg.kilometers_hi is not None
            else None
        )
        note = selected.selection_note
    return LegEmission(
        leg=leg.leg,
        vehicle_run=leg.vehicle_run,
        mode=leg.mode,
        basis=leg.basis,
        kilometers_lo=leg.kilometers_lo,
        kilometers_hi=leg.kilometers_hi,
        factor_key=factor_key,
        factor_version=factor_version,
        rate_low=rate_low,
        rate_high=rate_high,
        emission_low=low,
        emission_high=high,
        linked_leg=leg.linked_leg,
        flags=flags,
        selection_note=note,
    )


def _capacity_units(boarding: Boarding) -> int:
    return boarding.children + boarding.adults + boarding.wheelchair


def allocate_leg(
    data: Dataset,
    leg: Leg,
    emission: LegEmission,
    leg_emissions: dict[str, LegEmission],
) -> list[TourClaim]:
    """按共同容量份额分配一个车段；空驶段复用关联载客段的份额。"""
    if leg.linked_leg:
        anchor = data.leg(leg.linked_leg)
        boardings = data.boardings_on(anchor.leg)
        source_leg_id = anchor.leg
    else:
        boardings = data.boardings_on(leg.leg)
        source_leg_id = leg.leg

    total_units = sum(_capacity_units(b) for b in boardings)
    claims: list[TourClaim] = []
    for boarding in boardings:
        units = _capacity_units(boarding)
        share = units / total_units
        if emission.emission_low is None or emission.emission_high is None:
            claim_low = claim_high = None
        else:
            claim_low = emission.emission_low * share
            claim_high = emission.emission_high * share
        # 载客段自身的 flags 与（空驶）关联段 flags 合并，保证不确定来源不丢
        merged_flags = tuple(
            sorted(set(emission.flags) | set(leg_emissions[source_leg_id].flags))
        )
        claims.append(
            TourClaim(
                leg=leg.leg,
                tour=boarding.tour,
                children=boarding.children,
                capacity_units=units,
                share=share,
                claim_low=claim_low,
                claim_high=claim_high,
                flags=merged_flags,
            )
        )

    # 守恒校验：份额之和为 1；有数值时各团认领之和等于车段排放
    if claims:
        share_sum = sum(c.share for c in claims)
        if abs(share_sum - 1.0) > EPSILON:
            raise AssertionError(f"车段 {leg.leg} 份额之和 {share_sum} != 1")
        if emission.emission_low is not None and emission.emission_high is not None:
            total_low = sum(c.claim_low for c in claims)
            total_high = sum(c.claim_high for c in claims)
            if (
                abs(total_low - emission.emission_low) > EPSILON
                or abs(total_high - emission.emission_high) > EPSILON
            ):
                raise AssertionError(
                    f"车段 {leg.leg} 分配合计 {total_low}/{total_high} "
                    f"!= 车段排放 {emission.emission_low}/{emission.emission_high}"
                )
    return claims


def build_run_register(
    data: Dataset, pin: FactorPin
) -> dict[str, LegEmission]:
    """计算全部车段排放（服务车次台账），供共享车段复用与守恒核查。"""
    register: dict[str, LegEmission] = {}
    # 先算载客段，空驶段合并 flag 时需要关联段结果
    for leg in sorted(data.legs, key=lambda l: (l.linked_leg != "", l.leg)):
        register[leg.leg] = compute_leg_emission(data, leg, pin)
    return register


def offer_footprint(
    data: Dataset,
    offer_id: str,
    pin: FactorPin,
    register: dict[str, LegEmission] | None = None,
) -> OfferFootprint:
    offer = data.offer(offer_id)
    if register is None:
        register = build_run_register(data, pin)

    claims: list[TourClaim] = []
    leg_flags_map: dict[str, tuple[str, ...]] = {}
    for leg_id in offer.legs:
        leg = data.leg(leg_id)
        emission = register[leg_id]
        leg_claims = allocate_leg(data, leg, emission, register)
        tour_claim = next((c for c in leg_claims if c.tour == offer.tour), None)
        if tour_claim is None:  # 已由引用校验兜底，这里显式防御
            raise ValueError(f"offer {offer_id} 的团 {offer.tour} 未在 {leg_id} 出现")
        claims.append(tour_claim)
        leg_flags_map[leg_id] = tour_claim.flags

    numeric = [c for c in claims if c.claim_low is not None]
    if len(numeric) == len(claims):
        total_low = sum(c.claim_low for c in claims)
        total_high = sum(c.claim_high for c in claims)
        children = claims[0].children
        per_child_low = total_low / children
        per_child_high = total_high / children
    else:
        per_child_low = per_child_high = None

    all_flags = {flag for flags in leg_flags_map.values() for flag in flags}
    blocking = tuple(
        sorted(
            all_flags
            & {
                "mode_unconfirmed",
                "factor_missing",
                "distance_pending",
            }
        )
    )
    assumptions = tuple(sorted(all_flags & {"boarding_estimated", "plan_basis"}))
    return OfferFootprint(
        offer=offer_id,
        tour=offer.tour,
        legs=offer.legs,
        children=claims[0].children,
        per_child_low=per_child_low,
        per_child_high=per_child_high,
        leg_flags=leg_flags_map,
        blocking_flags=blocking,
        assumption_flags=assumptions,
        tour_claims=claims,
    )
