"""登记基准的排放构建与同等服务口径门槛。

基准不是拿来就能当减排证据，必须与本次售卖所适用的条件同口径：

- event_key 对应同一研学服务资格；地区一致；
- 出入场时窗必须覆盖本次售卖的签到/最迟接回时刻（基准 incoming 不晚于
  offer incoming，基准 outgoing 不早于 offer outgoing）；
- 轮椅位可达性：offer 要求 wheelchair 时基准也必须提供；
- 容量门槛：基准 size 不低于本次售卖登记的最低成团人数（否则基准服务
  根本装不下本团，没提供同等服务）；
- 基准必须在比较时点前已登记（active_since <= as_of）；
- 基准自身核算所需因子可得。
未通过任一门槛的基准只能记录“不采用原因”，不能成为减排证据。

共享车次（benchmark.shared_legs）：该段在双方计算中必须取同一里程与同一
因子版本。实现上直接复用 offer 侧车段台账（同一 FactorPin + 同一 leg 记录），
从机制上排除“分别取对各自有利的未知值”。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .accounting import LegEmission
from .factors import FactorPin
from .model import Benchmark, Dataset, Offer


@dataclass
class GateResult:
    reference: str
    passed: bool
    reasons: tuple[str, ...]


@dataclass
class BenchmarkFootprint:
    reference: str
    per_child_low: float | None
    per_child_high: float | None
    components: list[dict[str, object]] = field(default_factory=list)
    factor_missing: bool = False
    gate: GateResult | None = None


def eligibility_gate(
    data: Dataset,
    offer: Offer,
    benchmark: Benchmark,
    as_of: date,
    factor_missing: bool,
) -> GateResult:
    reasons: list[str] = []
    if benchmark.event_key != offer.route:
        reasons.append(
            f"event_key {benchmark.event_key} 与路线 {offer.route} 非同一研学服务资格"
        )
    if benchmark.region != data.leg(offer.legs[0]).region:
        reasons.append("地区不一致")
    if offer.accessibility == "wheelchair" and benchmark.access != "wheelchair":
        reasons.append("本团需要轮椅位而基准未提供同等可达性")
    if benchmark.incoming > offer.incoming:
        reasons.append(
            f"基准签到 {benchmark.incoming:%H:%M} 晚于本团 {offer.incoming:%H:%M}，时窗不覆盖"
        )
    if benchmark.outgoing < offer.outgoing:
        reasons.append(
            f"基准最迟接回 {benchmark.outgoing:%H:%M} 早于本团 {offer.outgoing:%H:%M}，时窗不覆盖"
        )
    if benchmark.size < offer.minimum_size:
        reasons.append(
            f"基准容量 {benchmark.size} 低于最低成团人数 {offer.minimum_size}"
        )
    if benchmark.active_since > as_of:
        reasons.append(
            f"基准登记日 {benchmark.active_since} 晚于比较时点 {as_of}"
        )
    if factor_missing:
        reasons.append("基准交通方式缺少适用正式因子")
    return GateResult(
        reference=benchmark.reference, passed=not reasons, reasons=tuple(reasons)
    )


def benchmark_footprint(
    data: Dataset,
    offer: Offer,
    benchmark: Benchmark,
    pin: FactorPin,
    register: dict[str, LegEmission],
    as_of: date,
) -> BenchmarkFootprint:
    components: list[dict[str, object]] = []
    missing = False

    # 基准自有服务里程（非共享部分）
    selected = pin.select(benchmark.region, benchmark.mode)
    if selected is None:
        missing = True
        own_low = own_high = None
        components.append(
            {
                "part": "own",
                "mode": benchmark.mode,
                "km_low": benchmark.kilometers_low,
                "km_high": benchmark.kilometers_high,
                "factor": None,
                "emission_low": None,
                "emission_high": None,
            }
        )
    else:
        factor = selected.factor
        own_low = benchmark.kilometers_low * factor.rate_low
        own_high = (
            benchmark.kilometers_high * factor.rate_high
            if benchmark.kilometers_high is not None
            else benchmark.kilometers_low * factor.rate_high
        )
        components.append(
            {
                "part": "own",
                "mode": benchmark.mode,
                "km_low": benchmark.kilometers_low,
                "km_high": benchmark.kilometers_high,
                "factor": f"{factor.factor} v{factor.version}",
                "emission_low": own_low,
                "emission_high": own_high,
            }
        )

    # 共享运营段：复用 offer 侧同一 leg 台账记录，因子/里程完全一致
    shared_low = shared_high = 0.0
    for leg_id in benchmark.shared_legs:
        shared_leg = register[leg_id]
        components.append(
            {
                "part": "shared",
                "leg": leg_id,
                "mode": shared_leg.mode,
                "km_low": shared_leg.kilometers_lo,
                "km_high": shared_leg.kilometers_hi,
                "factor": (
                    f"{shared_leg.factor_key} v{shared_leg.factor_version}"
                    if shared_leg.factor_key
                    else None
                ),
                "emission_low": shared_leg.emission_low,
                "emission_high": shared_leg.emission_high,
            }
        )
        if shared_leg.emission_low is None:
            missing = True
        else:
            shared_low += shared_leg.emission_low
            shared_high += shared_leg.emission_high

    if missing:
        per_low = per_high = None
    else:
        per_low = (own_low + shared_low) / benchmark.size
        per_high = (own_high + shared_high) / benchmark.size

    footprint = BenchmarkFootprint(
        reference=benchmark.reference,
        per_child_low=per_low,
        per_child_high=per_high,
        components=components,
        factor_missing=missing,
    )
    footprint.gate = eligibility_gate(data, offer, benchmark, as_of, missing)
    return footprint
