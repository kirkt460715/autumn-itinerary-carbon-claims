"""因子选择与比较轮钉版。

规则（对应资料约定与业务要求）：

1. 因子按日期、地区和车种(mode)兼容；同一 factor_key 的不同 version 是
   同一共同参数的不同发档，一轮比较中只能选定一个版本，不能让两条路线
   分别取对自己有利的值。
2. 一轮比较（一个 offer 对一个基准）在 as-of 时点，对所有用到的
   (factor, mode) 选定“截至 as-of 已发档且适用期覆盖服务日”的最高版本，
   形成只读的 FactorPin。共享车段在双方计算中通过同一缓存复用，数值完全一致。
3. 找不到适用因子时返回 None，由核算层登记“正式因子缺失”这一不确定来源，
   不能静默替换为其他车种因子。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .model import Dataset, Factor


class FactorUnavailable(Exception):
    """正式因子缺失（无适用发档）。"""

    def __init__(self, region: str, mode: str, day: date):
        super().__init__(f"缺少适用因子: region={region} mode={mode} day={day}")
        self.region = region
        self.mode = mode
        self.day = day


@dataclass(frozen=True)
class SelectedFactor:
    factor: Factor
    selection_note: str


class FactorPin:
    """一轮比较内的共同因子选择结果。

    选择是确定性的：as-of 时点、服务日、地区、mode 相同则结果相同，
    且对 offer 与 benchmark 一视同仁。
    """

    def __init__(self, data: Dataset, as_of: date, service_day: date):
        self._data = data
        self._as_of = as_of
        self._service_day = service_day
        self._cache: dict[tuple[str, str], SelectedFactor | None] = {}
        self.missing: list[tuple[str, str]] = []

    @property
    def as_of(self) -> date:
        return self._as_of

    @property
    def service_day(self) -> date:
        return self._service_day

    def select(self, region: str, mode: str) -> SelectedFactor | None:
        key = (region, mode)
        if key in self._cache:
            return self._cache[key]
        candidates = [
            f
            for f in self._data.factors
            if f.region == region
            and f.mode == mode
            and f.documented_at <= self._as_of
            and f.valid_from <= self._service_day <= f.valid_through
        ]
        if not candidates:
            self._cache[key] = None
            self.missing.append(key)
            return None
        chosen = max(candidates, key=lambda f: (f.version, f.documented_at))
        note = (
            f"as_of={self._as_of.isoformat()} 在 {len(candidates)} 个适用发档中"
            f"选定 {chosen.factor} v{chosen.version}（发档 "
            f"{chosen.documented_at.isoformat()}）"
        )
        selected = SelectedFactor(chosen, note)
        self._cache[key] = selected
        return selected

    def describe(self) -> dict[str, str]:
        """供结果回查：每个 (region, mode) 选了哪个版本、为什么。"""
        return {
            f"{region}|{mode}": (
                f"{s.factor.factor} v{s.factor.version}" if s else "MISSING"
            )
            for (region, mode), s in self._cache.items()
        }


def factor_key_for_mode(mode: str) -> str:
    """mode 到 factor 表 factor_key 的映射依据因子表登记的 mode 列。

    因子表本身以 mode 列登记适用车种，因此直接以 mode 参与选择；
    未登记的 mode（如 unconfirmed）自然落入缺失分支。
    """
    return mode
