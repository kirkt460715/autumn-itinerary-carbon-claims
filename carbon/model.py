"""源表加载、类型化记录与引用完整性校验。

所有表均来自供应商/登记方提交的 CSV（不可信数据）。本模块只做结构化
加载与跨表引用校验，业务核算在后续模块完成，保证“实际运行与订购时的
载客假设能回查”。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

TABLE_FIELDS = {
    "factors": (
        "factor version region mode valid_from valid_through "
        "rate_low rate_high documented_at"
    ).split(),
    "factors_key": ["factor", "version"],
    "legs": (
        "leg vehicle_run day region mode kilometers_lo kilometers_hi "
        "linked_leg basis received_on"
    ).split(),
    "legs_key": ["leg"],
    "boardings": "leg tour children adults wheelchair basis".split(),
    "boardings_key": ["leg", "tour"],
    "offers": (
        "offer route legs tour example_size minimum_size incoming outgoing "
        "accessibility sell_from sell_until"
    ).split(),
    "offers_key": ["offer"],
    "benchmarks": (
        "reference event_key region vehicle mode kilometers_low "
        "kilometers_high size incoming outgoing access active_since shared_legs"
    ).split(),
    "benchmarks_key": ["reference"],
    "changes": "notice offer action tour affected_leg effective_at received_at".split(),
    "changes_key": ["notice"],
    "labels": "label offer reference minimum_saving state issued_at valid_window".split(),
    "labels_key": ["label"],
    "cards": (
        "card edition offer parent_card cohort state active_from active_until"
    ).split(),
    "cards_key": ["card"],
}


def _d(value: str) -> date:
    return date.fromisoformat(value) if value else None


def _t(value: str) -> time | None:
    if not value:
        return None
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _i(value: str) -> int | None:
    return int(value) if value else None


def _f(value: str) -> float | None:
    return float(value) if value else None


@dataclass(frozen=True)
class Factor:
    factor: str
    version: int
    region: str
    mode: str
    valid_from: date
    valid_through: date
    rate_low: float
    rate_high: float
    documented_at: date


@dataclass(frozen=True)
class Leg:
    leg: str
    vehicle_run: str
    day: date
    region: str
    mode: str
    kilometers_lo: float | None
    kilometers_hi: float | None
    linked_leg: str
    basis: str  # actual | plan
    received_on: date


@dataclass(frozen=True)
class Boarding:
    leg: str
    tour: str
    children: int
    adults: int
    wheelchair: int
    basis: str  # confirmed | estimated


@dataclass(frozen=True)
class Offer:
    offer: str
    route: str
    legs: tuple[str, ...]
    tour: str
    example_size: int
    minimum_size: int
    incoming: time
    outgoing: time
    accessibility: str
    sell_from: date
    sell_until: date


@dataclass(frozen=True)
class Benchmark:
    reference: str
    event_key: str
    region: str
    vehicle: str
    mode: str
    kilometers_low: float
    kilometers_high: float | None
    size: int
    incoming: time
    outgoing: time
    access: str
    active_since: date
    shared_legs: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    notice: str
    offer: str
    action: str
    tour: str
    affected_leg: str
    effective_at: date
    received_at: date


@dataclass(frozen=True)
class Label:
    label: str
    offer: str
    reference: str
    minimum_saving: float
    state: str  # used | active | requested
    issued_at: date | None
    valid_window: tuple[date, date]


@dataclass(frozen=True)
class Card:
    card: str
    edition: str
    offer: str
    parent_card: str
    cohort: str
    state: str
    active_from: date
    active_until: date


@dataclass
class Dataset:
    factors: list[Factor]
    legs: list[Leg]
    boardings: list[Boarding]
    offers: list[Offer]
    benchmarks: list[Benchmark]
    changes: list[Change]
    labels: list[Label]
    cards: list[Card]
    _leg_index: dict[str, Leg] = field(default_factory=dict, repr=False)
    _factor_index: dict[tuple[str, int], Factor] = field(
        default_factory=dict, repr=False
    )

    def leg(self, leg_id: str) -> Leg:
        return self._leg_index[leg_id]

    def factor(self, key: str, version: int) -> Factor:
        return self._factor_index[(key, version)]

    def boardings_on(self, leg_id: str) -> list[Boarding]:
        return [b for b in self.boardings if b.leg == leg_id]

    def offer(self, offer_id: str) -> Offer:
        for o in self.offers:
            if o.offer == offer_id:
                return o
        raise KeyError(offer_id)

    def benchmark(self, ref: str) -> Benchmark:
        for b in self.benchmarks:
            if b.reference == ref:
                return b
        raise KeyError(ref)


def _read_rows(path: Path, expected: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected:
            raise ValueError(f"{path.name}: 表头 {reader.fieldnames} 与约定 {expected} 不符")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"{path.name}: 空表")
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"{path.name}: 存在未声明列或缺失字段")
    return rows


def _check_unique(rows: list[dict[str, str]], key: list[str], name: str) -> None:
    seen = set()
    for row in rows:
        value = tuple(row[k] for k in key)
        if not all(value):
            raise ValueError(f"{name}: 记录键存在空值 {value}")
        if value in seen:
            raise ValueError(f"{name}: 记录键重复 {value}")
        seen.add(value)


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    raw: dict[str, list[dict[str, str]]] = {}
    for name in (
        "factors",
        "legs",
        "boardings",
        "offers",
        "benchmarks",
        "changes",
        "labels",
        "cards",
    ):
        rows = _read_rows(data_dir / f"{name}.csv", TABLE_FIELDS[name])
        _check_unique(rows, TABLE_FIELDS[f"{name}_key"], name)
        raw[name] = rows

    factors = [
        Factor(
            factor=r["factor"],
            version=int(r["version"]),
            region=r["region"],
            mode=r["mode"],
            valid_from=_d(r["valid_from"]),
            valid_through=_d(r["valid_through"]),
            rate_low=_f(r["rate_low"]),
            rate_high=_f(r["rate_high"]),
            documented_at=_d(r["documented_at"]),
        )
        for r in raw["factors"]
    ]
    legs = [
        Leg(
            leg=r["leg"],
            vehicle_run=r["vehicle_run"],
            day=_d(r["day"]),
            region=r["region"],
            mode=r["mode"],
            kilometers_lo=_f(r["kilometers_lo"]),
            kilometers_hi=_f(r["kilometers_hi"]),
            linked_leg=r["linked_leg"],
            basis=r["basis"],
            received_on=_d(r["received_on"]),
        )
        for r in raw["legs"]
    ]
    boardings = [
        Boarding(
            leg=r["leg"],
            tour=r["tour"],
            children=int(r["children"]),
            adults=int(r["adults"]),
            wheelchair=int(r["wheelchair"]),
            basis=r["basis"],
        )
        for r in raw["boardings"]
    ]
    offers = [
        Offer(
            offer=r["offer"],
            route=r["route"],
            legs=tuple(r["legs"].split("|")),
            tour=r["tour"],
            example_size=int(r["example_size"]),
            minimum_size=int(r["minimum_size"]),
            incoming=_t(r["incoming"]),
            outgoing=_t(r["outgoing"]),
            accessibility=r["accessibility"],
            sell_from=_d(r["sell_from"]),
            sell_until=_d(r["sell_until"]),
        )
        for r in raw["offers"]
    ]
    benchmarks = []
    for r in raw["benchmarks"]:
        benchmarks.append(
            Benchmark(
                reference=r["reference"],
                event_key=r["event_key"],
                region=r["region"],
                vehicle=r["vehicle"],
                mode=r["mode"],
                kilometers_low=_f(r["kilometers_low"]),
                kilometers_high=_f(r["kilometers_high"]) or None,
                size=int(r["size"]),
                incoming=_t(r["incoming"]),
                outgoing=_t(r["outgoing"]),
                access=r["access"],
                active_since=_d(r["active_since"]),
                shared_legs=tuple(
                    r["shared_legs"].split("|") if r["shared_legs"] else []
                ),
            )
        )
    changes = [
        Change(
            notice=r["notice"],
            offer=r["offer"],
            action=r["action"],
            tour=r["tour"],
            affected_leg=r["affected_leg"],
            effective_at=_d(r["effective_at"]),
            received_at=_d(r["received_at"]),
        )
        for r in raw["changes"]
    ]
    labels = []
    for r in raw["labels"]:
        window = r["valid_window"].split("/")
        labels.append(
            Label(
                label=r["label"],
                offer=r["offer"],
                reference=r["reference"],
                minimum_saving=float(r["minimum_saving"]),
                state=r["state"],
                issued_at=_d(r["issued_at"]),
                valid_window=(_d(window[0]), _d(window[1])),
            )
        )
    cards = [
        Card(
            card=r["card"],
            edition=r["edition"],
            offer=r["offer"],
            parent_card=r["parent_card"],
            cohort=r["cohort"],
            state=r["state"],
            active_from=_d(r["active_from"]),
            active_until=_d(r["active_until"]),
        )
        for r in raw["cards"]
    ]

    dataset = Dataset(
        factors=factors,
        legs=legs,
        boardings=boardings,
        offers=offers,
        benchmarks=benchmarks,
        changes=changes,
        labels=labels,
        cards=cards,
        _leg_index={l.leg: l for l in legs},
        _factor_index={(f.factor, f.version): f for f in factors},
    )
    _validate_references(dataset)
    return dataset


def _validate_references(data: Dataset) -> None:
    """跨表引用与业务主键完整性。"""
    # legs: 空驶 linked_leg 必须指向同车次真实载客段，且不能成环
    for leg in data.legs:
        if leg.linked_leg:
            target = data._leg_index.get(leg.linked_leg)
            if target is None:
                raise ValueError(f"legs {leg.leg}: linked_leg {leg.linked_leg} 不存在")
            if target.vehicle_run != leg.vehicle_run:
                raise ValueError(
                    f"legs {leg.leg}: 空驶关联段 {leg.linked_leg} 不属于同一车次"
                )
            if target.linked_leg:
                raise ValueError(f"legs {leg.leg}: 空驶只能关联到载客段，不能链式关联")
        if leg.kilometers_lo is not None and leg.kilometers_hi is not None:
            if leg.kilometers_hi < leg.kilometers_lo:
                raise ValueError(f"legs {leg.leg}: 里程上界小于下界")

    # boardings: 必须落在已登记车段上
    for boarding in data.boardings:
        if boarding.leg not in data._leg_index:
            raise ValueError(f"boardings: 车段 {boarding.leg} 未在 legs 登记")

    # 同一 (leg, tour) 已由键唯一保证；同一车段必须能出现多团合乘（业务前提）
    by_leg: dict[str, set[str]] = {}
    for boarding in data.boardings:
        by_leg.setdefault(boarding.leg, set()).add(boarding.tour)
    if not any(len(tours) > 1 for tours in by_leg.values()):
        raise ValueError("boardings: 缺少合乘车段证据")

    # offers: 路线车段存在；载客段要求该团有载客记录，
    # 空驶段（linked_leg 非空）按设计不登记 boarding，归属由关联载客段承接
    for offer in data.offers:
        for leg_id in offer.legs:
            leg = data._leg_index.get(leg_id)
            if leg is None:
                raise ValueError(f"offers {offer.offer}: 车段 {leg_id} 未登记")
            anchor_id = leg.linked_leg or leg_id
            tours = {b.tour for b in data.boardings if b.leg == anchor_id}
            if offer.tour not in tours:
                raise ValueError(
                    f"offers {offer.offer}: 团 {offer.tour} 在 {anchor_id} 无载客记录"
                )

    # benchmarks: 共享车段必须指向真实 legs 记录
    for benchmark in data.benchmarks:
        for leg_id in benchmark.shared_legs:
            if leg_id not in data._leg_index:
                raise ValueError(
                    f"benchmarks {benchmark.reference}: 共享车段 {leg_id} 未登记"
                )

    # changes: offer/leg 可回查
    for change in data.changes:
        if not any(o.offer == change.offer for o in data.offers):
            raise ValueError(f"changes {change.notice}: offer {change.offer} 未登记")
        if change.affected_leg not in data._leg_index:
            raise ValueError(
                f"changes {change.notice}: 车段 {change.affected_leg} 未登记"
            )

    # labels/cards 关联
    offer_ids = {o.offer for o in data.offers}
    benchmark_ids = {b.reference for b in data.benchmarks}
    for label in data.labels:
        if label.offer not in offer_ids:
            raise ValueError(f"labels {label.label}: offer 未登记")
        if label.reference not in benchmark_ids:
            raise ValueError(f"labels {label.label}: 基准未登记")
        if label.valid_window[1] < label.valid_window[0]:
            raise ValueError(f"labels {label.label}: valid_window 倒置")
    card_ids = {c.card for c in data.cards}
    for card in data.cards:
        if card.offer not in offer_ids:
            raise ValueError(f"cards {card.card}: offer 未登记")
        if card.parent_card and card.parent_card not in card_ids:
            raise ValueError(f"cards {card.card}: 父卡 {card.parent_card} 未登记")
