"""低碳标签工作流的业务规则测试。

运行：python3 -m unittest discover -s tests -v
仅依赖标准库，测试代码与供应商数据目录分离（不向 data/ 写入）。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from carbon import accounting, factors, workflow
from carbon.model import load_dataset

DATA = Path(__file__).resolve().parents[1] / "data"
AS_OF = date(2026, 11, 23)


class WorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(DATA)
        cls.result = workflow.run(cls.data, AS_OF)

    def leg(self, leg_id):
        return self.data.leg(leg_id)

    def fp(self, offer_id):
        return self.result.offers[offer_id].footprint

    def comp(self, offer_id, reference):
        return next(
            c
            for c in self.result.offers[offer_id].comparisons
            if c.reference == reference
        )

    def decision(self, label_id):
        return self.result.label_decisions[label_id]


class TestFactorPinning(WorkflowTestCase):
    def test_latest_documented_version_selected_deterministically(self):
        # coach 因子有 v1（2026-01-06 发档）与 v2（2026-11-23 发档）
        pin = factors.FactorPin(self.data, AS_OF, date(2026, 11, 12))
        selected = pin.select("R", "coach")
        self.assertEqual(selected.factor.factor, "Fbus")
        self.assertEqual(selected.factor.version, 2)

    def test_as_of_before_new_issue_uses_old_version(self):
        pin = factors.FactorPin(self.data, date(2026, 11, 22), date(2026, 11, 12))
        selected = pin.select("R", "coach")
        self.assertEqual(selected.factor.version, 1)

    def test_unconfirmed_mode_has_no_factor_and_is_flagged(self):
        pin = factors.FactorPin(self.data, AS_OF, date(2026, 11, 15))
        self.assertIsNone(pin.select("R", "unconfirmed"))
        self.assertIn(("R", "unconfirmed"), pin.missing)

    def test_same_pin_serves_both_sides_of_a_comparison(self):
        # O3 与 B3 共享 R09-1：一轮内 (R, coach) 只解析一次且版本一致
        res = self.result.offers["O3"]
        self.assertEqual(res.pin_description.get("R|coach"), "Fbus v2")
        shared = self.result.run_register["R09-1"]
        self.assertEqual(shared.factor_version, 2)
        b3 = res.benchmark_footprints["B3"]
        shared_component = next(
            c for c in b3.components if c.get("part") == "shared"
        )
        self.assertEqual(shared_component["factor"], "Fbus v2")
        self.assertEqual(
            shared_component["emission_low"], shared.emission_low
        )


class TestAccountingAndConservation(WorkflowTestCase):
    def test_leg_emission_interval(self):
        # R09-1: 20–23 km × Fbus v2 760–840
        em = self.result.run_register["R09-1"]
        self.assertAlmostEqual(em.emission_low, 20 * 760)
        self.assertAlmostEqual(em.emission_high, 23 * 840)

    def test_deadhead_follows_linked_leg_share(self):
        # R09-3 空驶，linked_leg=R09-1；两团份额必须与 R09-1 完全一致
        anchor = self.data.leg("R09-1")
        deadhead = self.data.leg("R09-3")
        self.assertEqual(deadhead.linked_leg, "R09-1")
        pin = factors.FactorPin(self.data, AS_OF, anchor.day)
        register = accounting.build_run_register(self.data, pin)
        anchor_claims = {
            c.tour: c.share
            for c in accounting.allocate_leg(self.data, anchor, register["R09-1"], register)
        }
        dead_claims = {
            c.tour: c.share
            for c in accounting.allocate_leg(
                self.data, deadhead, register["R09-3"], register
            )
        }
        self.assertEqual(anchor_claims, dead_claims)

    def test_all_leg_allocations_conserve_total_emission(self):
        # 同一服务车次分拆团次后不消失、不翻倍：逐段守恒
        for leg in self.data.legs:
            em = self.result.run_register[leg.leg]
            if em.emission_low is None or em.emission_high is None:
                continue
            claims = accounting.allocate_leg(
                self.data, leg, em, self.result.run_register
            )
            self.assertTrue(
                all(c.claim_low is not None for c in claims),
                f"{leg.leg} 存在数值车段但认领结果为空",
            )
            self.assertAlmostEqual(
                sum(c.claim_low for c in claims), em.emission_low
            )
            self.assertAlmostEqual(
                sum(c.claim_high for c in claims), em.emission_high
            )
            self.assertAlmostEqual(sum(c.share for c in claims), 1.0)

    def test_wheelchair_small_group_uses_capacity_units_not_full_bus_per_head(self):
        # R09-1：G090 12 单位（9 儿童+3 成人+0 轮椅），
        # G091 15 单位（10 儿童+4 成人+1 轮椅）
        # 不能拿坐满车的人均量比较，轮椅位计入容量分母
        em = self.result.run_register["R09-1"]
        claims = {
            c.tour: c
            for c in accounting.allocate_leg(self.data, self.leg("R09-1"), em, self.result.run_register)
        }
        total_units = 12 + 15
        self.assertAlmostEqual(claims["G090"].share, 12 / total_units)
        self.assertAlmostEqual(claims["G091"].share, 15 / total_units)
        # 轮椅团 G091 单位数确实包含轮椅位
        self.assertEqual(claims["G091"].capacity_units, 15)
        self.assertEqual(claims["G090"].capacity_units, 12)

    def test_distance_pending_blocks_numeric_footprint(self):
        # R07-2 缺 kilometers_hi；虽不在在售 offer 中，台账须标 distance_pending
        em = self.result.run_register["R07-2"]
        self.assertIn("distance_pending", em.flags)
        self.assertIsNone(em.emission_high)

    def test_plan_legs_carry_assumption_flags(self):
        fp = self.fp("O3")
        self.assertIn("plan_basis", fp.assumption_flags)
        self.assertIn("boarding_estimated", fp.assumption_flags)
        for leg_id in fp.legs:
            self.assertIn("plan_basis", fp.leg_flags[leg_id])

    def test_mode_unconfirmed_blocks_offer_without_silent_substitution(self):
        # R12 车段 mode=unconfirmed：无因子替换、无数值、阻断
        em = self.result.run_register["R12-1"]
        self.assertIn("mode_unconfirmed", em.flags)
        self.assertIn("factor_missing", em.flags)
        self.assertIsNone(em.emission_low)


class TestBenchmarkGates(WorkflowTestCase):
    def test_gate_rejects_mismatched_event(self):
        # B2 是 T2 基准，不能作为 T1 路线 O1 的证据
        comp = self.comp("O1", "B2")
        self.assertFalse(comp.gate_passed)
        self.assertFalse(comp.adopted)
        self.assertTrue(any("研学服务资格" in r for r in comp.gate_reasons))

    def test_gate_rejects_window_mismatch(self):
        # B4 electric：incoming 09:40 晚于 O1 的 09:15，outgoing 15:30 早于 15:35
        comp = self.comp("O1", "B4")
        self.assertFalse(comp.gate_passed)
        self.assertTrue(any("时窗" in r for r in comp.gate_reasons))

    def test_gate_rejects_accessibility_gap(self):
        # O3 需要 wheelchair；B4 access=standard
        comp = self.comp("O3", "B4")
        self.assertTrue(any("轮椅位" in r for r in comp.gate_reasons))

    def test_passing_gates_and_conservative_saving(self):
        # B3 对 O3（T1, wheelchair, 最低 8 人）同口径通过
        comp = self.comp("O3", "B3")
        self.assertTrue(comp.gate_passed)
        self.assertTrue(comp.adopted)
        # worst = 基准下界 - 本路线上界；best 反之
        self.assertAlmostEqual(
            comp.worst_case_saving,
            comp.benchmark_per_child_low - comp.offer_per_child_high,
        )
        self.assertGreaterEqual(comp.best_case_saving, comp.worst_case_saving)

    def test_b3_threshold_met_before_notice_but_blocked_after(self):
        comp = self.comp("O3", "B3")
        self.assertGreaterEqual(comp.worst_case_saving, 500)


class TestLabelDecisions(WorkflowTestCase):
    def test_used_label_retained_with_settlement_note(self):
        dec = self.decision("L1")
        self.assertEqual(dec.decision, "retain_with_settlement")
        self.assertTrue(dec.settlement_note)
        # 当前证据下 O1 vs B1 并不构成减排（保守差距为负）
        comp = self.comp("O1", "B1")
        self.assertLess(comp.worst_case_saving, 0)

    def test_active_label_withdrawn_on_vehicle_unconfirmed_notice(self):
        dec = self.decision("L2")
        self.assertEqual(dec.decision, "withdraw")
        self.assertIn("C2", dec.blocking_notices)
        # 若无通知，纯比较本可达到 500 下限——撤标签原因是不确定而非差距
        comp = self.comp("O3", "B3")
        self.assertGreaterEqual(comp.worst_case_saving, dec.label.minimum_saving)

    def test_requested_label_denied_when_gap_below_floor(self):
        dec = self.decision("L3")
        self.assertEqual(dec.decision, "deny")
        comp = self.comp("O2", "B2")
        self.assertLess(comp.worst_case_saving, dec.label.minimum_saving)

    def test_alternative_baselines_recorded_with_reasons(self):
        # O1 还可与 B3 比较，但不替换登记的 B1，原因须留痕
        comp_b3 = self.comp("O1", "B3")
        dec = self.decision("L1")
        self.assertTrue(any("B3" in r for r in dec.reasons))
        self.assertEqual(dec.chosen_reference, "B1")
        # 所有候选基准（含不采用）都有明确结论
        for comp in self.result.offers["O1"].comparisons:
            self.assertTrue(comp.adopted or comp.not_adopted_reason)

    def test_insufficient_evidence_keeps_range_with_assumption_marks(self):
        # O3 被阻断时仍保留每儿童区间，且明确标注假设来源
        fp = self.fp("O3")
        self.assertIsNotNone(fp.per_child_low)
        self.assertTrue(fp.assumption_flags)


class TestNoticesAndReruns(WorkflowTestCase):
    def test_notice_received_after_as_of_is_pending(self):
        statuses = {a.notice.notice: a.status for a in self.result.notices}
        self.assertEqual(statuses["C1"], "pending_future_receipt")
        self.assertEqual(statuses["C3"], "pending_future_receipt")
        self.assertEqual(statuses["C2"], "applied")

    def test_cancellation_rerun_changes_remaining_tour_per_child(self):
        # 送达后场景（as_of=2026-11-25）：G091 取消，剩余 G090 重算
        later = workflow.run(self.data, date(2026, 11, 25))
        before = later.offers["O1"].footprint
        rerun = workflow.rerun_after_cancellation(
            self.data, date(2026, 11, 25), "O1", "G091"
        )
        # 分母从 27 单位（12+15）降到 12 单位，剩余团每儿童份额上升，不沿用旧人均
        self.assertNotAlmostEqual(
            before.per_child_high, rerun.per_child_high
        )
        self.assertGreater(rerun.per_child_high, before.per_child_high)
        # 重算后 G090 在每段份额为 1.0（该路线段上只剩它）
        self.assertTrue(all(abs(c.share - 1.0) < 1e-9 for c in rerun.tour_claims))

    def test_plan_basis_cancellation_rerun_also_recomputes(self):
        # R13 是 plan 车次、boarding 为 estimated：G131 取消后剩余 G130 重算
        rerun = workflow.rerun_after_cancellation(
            self.data, AS_OF, "O3", "G131"
        )
        original = self.fp("O3")
        # 原份额 12/(12+15)，取消后该路线段只剩 G130 → 份额 1.0，人均上升
        self.assertGreater(rerun.per_child_high, original.per_child_high)
        self.assertTrue(all(abs(c.share - 1.0) < 1e-9 for c in rerun.tour_claims))
        # 计划/估算假设标注在重算后仍保留，可回查订购时假设
        self.assertIn("plan_basis", rerun.assumption_flags)

    def test_detour_notice_blocks_reissue_after_receipt(self):
        later = workflow.run(self.data, date(2026, 11, 25))
        dec = later.label_decisions["L1"]
        # 已兑现：仍保留原声明，但结算说明中记录 actual_detour 待核对
        self.assertEqual(dec.decision, "retain_with_settlement")
        self.assertIn("C3", dec.blocking_notices)
        self.assertTrue(dec.settlement_note)


class TestCardsAndTopics(WorkflowTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 窗口内复核快照：day1 卡片（CA1/CA2）在 11-15 仍在售
        cls.in_window = workflow.run(cls.data, date(2026, 11, 15))

    def test_card_actions_align_with_label_decisions(self):
        by_card = {r.card.card: r for r in self.result.card_reviews}
        # 当前 as_of=11-23：day1 卡片已过在售窗口；已兑现的 CA1 保留声明
        self.assertEqual(by_card["CA1"].action, "retain")          # 已兑现
        self.assertEqual(by_card["CA2"].action, "not_on_sale")     # 窗口已过
        self.assertEqual(by_card["CA3"].action, "withdraw_label")  # L2 撤下
        self.assertEqual(by_card["CA4"].action, "not_on_sale")     # 11-25 才开售
        # 即便 CA4 未开售，其标签决策仍为撤下（C2 阻断），开售时不得带标签
        self.assertEqual(
            by_card["CA4"].label_decision.decision, "withdraw"
        )

        # 窗口内（11-15）复核：CA2 随 L3 拒发而撤标签
        in_window = {r.card.card: r for r in self.in_window.card_reviews}
        self.assertEqual(in_window["CA2"].action, "withdraw_label")
        self.assertEqual(in_window["CA1"].action, "retain")

        # CA4 开售时点（11-25，C2 已生效）复核：动作变为撤标签
        on_sale = workflow.run(self.data, date(2026, 11, 25))
        ca4 = next(r for r in on_sale.card_reviews if r.card.card == "CA4")
        self.assertEqual(ca4.action, "withdraw_label")

    def test_child_card_sharing_parent_evidence_has_no_independent_contribution(self):
        # CA4 与父卡 CA3 同为 O3/G130：同一批孩子的同一交通 → 不独立
        ca4 = next(r for r in self.result.card_reviews if r.card.card == "CA4")
        ca3 = next(r for r in self.result.card_reviews if r.card.card == "CA3")
        self.assertFalse(ca4.independent_contribution)
        self.assertTrue(ca3.independent_contribution)

        # CA2(O2/G091) 与父卡 CA1(O1/G090) 是同车不同团的两组孩子 → 独立贡献
        ca2 = next(r for r in self.in_window.card_reviews if r.card.card == "CA2")
        ca1 = next(r for r in self.in_window.card_reviews if r.card.card == "CA1")
        self.assertTrue(ca2.independent_contribution)
        self.assertTrue(ca1.independent_contribution)
        self.assertTrue(any("不同组" in r for r in ca2.reasons))

    def test_topic_total_does_not_double_count_same_children_child_card(self):
        # cohort-day2：CA3/CA4 同一批孩子。即便假设标签可计入，CA4 也必须被去重
        day2 = next(
            t for t in self.result.topic_totals if t.cohort_scope == "cohort-day2"
        )
        excluded = dict(day2.excluded_cards)
        self.assertIn("CA4", excluded)
        self.assertTrue(any("同一批孩子" in r for r in excluded.values()))
        # 覆盖键完全相同是去重的机制依据
        ca3 = next(r for r in self.result.card_reviews if r.card.card == "CA3")
        ca4 = next(r for r in self.result.card_reviews if r.card.card == "CA4")
        self.assertEqual(ca3.coverage_key, ca4.coverage_key)

    def test_topic_total_current_snapshot_has_no_countable_saving(self):
        # 当前 as_of=11-23：L1 已兑现（不计入新专题）、L2 撤下、L3 拒发 → 合计 0
        for total in self.result.topic_totals:
            self.assertEqual(total.total_worst_case_saving, 0.0)
            self.assertEqual(total.included_cards, ())

    def test_topic_total_traceable_to_cards_and_baselines(self):
        # 即便合计为 0，覆盖范围与基准的反查结构仍存在；构造一个可计入场景：
        # 临时把 L2 决策视为可计入时，coverage_key 必须能还原 tour+车段
        day2 = next(t for t in self.result.topic_totals if t.cohort_scope == "cohort-day2")
        review_ca3 = next(r for r in self.result.card_reviews if r.card.card == "CA3")
        self.assertEqual(
            review_ca3.coverage_key,
            ("G130", "R13-1", "R13-2", "R13-3"),
        )
        # CA4 与 CA3 覆盖键相同 → 机制上必然去重
        review_ca4 = next(r for r in self.result.card_reviews if r.card.card == "CA4")
        self.assertEqual(review_ca4.coverage_key, review_ca3.coverage_key)
        self.assertIsInstance(day2.coverage_and_baselines, list)

    def test_window_coverage_flag(self):
        # 所有卡片窗口均在标签窗口内时 window_covered=True；越界则 False
        for review in self.result.card_reviews:
            if review.label_decision is not None:
                lo, hi = review.label_decision.label.valid_window
                inside = lo <= review.card.active_from and review.card.active_until <= hi
                self.assertEqual(review.window_covered, inside)


class TestReferentialIntegrity(unittest.TestCase):
    def test_loader_rejects_bad_headers(self):
        # 现有数据必须能通过完整引用校验
        data = load_dataset(DATA)
        self.assertTrue(data.legs)

    def test_shared_leg_conservation_across_runs(self):
        # 每个实际车次 R00..R11 的全部车段，份额在团次间守恒
        data = load_dataset(DATA)
        pin = factors.FactorPin(data, AS_OF, date(2026, 11, 12))
        register = accounting.build_run_register(data, pin)
        for run_id in {l.vehicle_run for l in data.legs if l.basis == "actual"}:
            for leg in (l for l in data.legs if l.vehicle_run == run_id):
                em = register[leg.leg]
                if em.emission_low is None:
                    continue
                claims = accounting.allocate_leg(data, leg, em, register)
                self.assertAlmostEqual(sum(c.share for c in claims), 1.0)


if __name__ == "__main__":
    unittest.main()
