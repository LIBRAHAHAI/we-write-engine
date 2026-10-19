"""B2 回归：dispatch 不等产物/卡完成即推进（2026-09-11 修复）。

缺陷：_do_dispatch 建完卡直接返回 step["next"] → state.step 指向尚未开工的下游步，
进程中断后断点合法性无法判定，dispatcher 会空跑下游卡（违反 charter S4）。

修复语义（两拍）：
  拍1：建卡 / 幂等命中在途卡 / 重开 done 卡 → next=None，留本步；
  拍2：卡 status=done → 推进 step["next"]。

覆盖：留步、推进、iteration 不被轮询刷爆、回流重开只一次、重开失败下一拍重试、
并行组全 done 才放行、中断重启不重做已完成步骤、跳步清残留标记、旧 JSON 兼容。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine

PIPELINE = Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml"
MAT_MEMBERS = ["ww_mat_news", "ww_mat_history", "ww_mat_classic",
               "ww_mat_law", "ww_mat_econ"]


class DispatchWaitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        ww_engine.INSTANCES_DIR = self.tmp  # 不碰真实 instances/

    def _eng(self, name: str = "test-b2") -> WeWriteEngine:
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = name
        eng.mode = "test"  # M3/Δ10：run() 消费
        eng.template = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        eng.instance_path = self.tmp / f"{name}.json"
        return eng

    def _state(self, eng, step: str, iteration: int = 0, **kw) -> None:
        st = {"instance": eng.instance, "step": step, "iteration": iteration,
              "history": [], "status": "running", "dispatched": {}}
        st.update(kw)
        eng._save_state(st)

    def _load(self, eng) -> dict:
        return json.loads(eng.instance_path.read_text(encoding="utf-8"))

    # ---- 拍1：留本步 ----
    def test_inflight_card_stays_on_step(self):
        """卡在途（ready）→ run 一次后 state.step 不变，且不重复建卡。"""
        eng = self._eng()
        self._state(eng, "ww_topic")
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_1", "status": "ready"}), \
             mock.patch.object(eng, "_create_card", return_value="t_nope") as mk:
            out = eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_topic")  # 留本步
        self.assertIn("卡在途，留本步等待", out)
        mk.assert_not_called()  # 幂等跳过建卡

    def test_every_inflight_status_stays(self):
        """ready/running/todo/blocked 四种在途态都不得推进。"""
        for status in ("ready", "running", "todo", "blocked"):
            with self.subTest(status=status):
                eng = self._eng(f"test-b2-{status}")
                self._state(eng, "ww_topic")
                with mock.patch.object(eng, "_resolve_step_card",
                                       return_value={"id": "t_1", "status": status}), \
                     mock.patch.object(eng, "_create_card", return_value="t_nope"):
                    eng.run()
                self.assertEqual(self._load(eng)["step"], "ww_topic")

    def test_create_card_stays_on_step(self):
        """无卡 → 建卡后留本步（B2 核心：建卡即推进就是缺陷本身）。"""
        eng = self._eng()
        self._state(eng, "ww_topic")
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_new") as mk:
            out = eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_topic")
        self.assertIn("建卡 t_new", out)
        self.assertIn("卡在途，留本步等待", out)
        mk.assert_called_once()
        self.assertEqual(st["iteration"], 1)  # 真派工才自增
        self.assertEqual(st["dispatched"], {"ww_topic": 1})  # 派发标记已落盘

    def test_wait_polls_do_not_inflate_iteration(self):
        """留步轮询是心跳空转：iteration 不得每拍自增（否则刷爆 max_iterations）。"""
        eng = self._eng()
        self._state(eng, "ww_topic", iteration=3)
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_1", "status": "running"}), \
             mock.patch.object(eng, "_create_card", return_value="t_nope"):
            for _ in range(5):
                eng.run()
        st = self._load(eng)
        self.assertEqual(st["iteration"], 3)
        self.assertEqual(st["step"], "ww_topic")

    # ---- 拍2：卡 done 才推进 ----
    def test_done_card_advances_to_next(self):
        eng = self._eng()
        self._state(eng, "ww_topic")
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_new"):
            eng.run()  # 拍1：建卡留步
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_new", "status": "done"}):
            out = eng.run()  # 拍2：卡 done → 推进
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_gate_topic")
        self.assertIn("done → 推进", out)
        # A2（20260920）：标记不再整体清空——推进后本步标记保留（打回才显式 pop），
        # 正常推进到达的下游步不再误判成"上一轮残留"而误重开。
        self.assertEqual(st["dispatched"], {"ww_topic": 1})

    # ---- 打回重做守卫（原 done 卡重开逻辑不得被破坏）----
    def test_reject_reopens_done_card_exactly_once(self):
        """回流进本步：done 卡重开重做 → 留步等新一轮 done，且不无限重开。"""
        eng = self._eng()
        self._state(eng, "ww_write")  # 拍板打回后进 ww_write，派发标记已随推进清空
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_w", "status": "done"}), \
             mock.patch.object(eng, "_create_card", return_value="t_w"), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen:
            out1 = eng.run()  # 拍1：重开 → 留步
            self.assertEqual(self._load(eng)["step"], "ww_write")
            self.assertIn("重开重做", out1)
            self.assertIn("卡在途，留本步等待", out1)
            mk_reopen.assert_called_once()
            # 拍2：重开后卡回到 ready → 仍在途，不得再次重开
            with mock.patch.object(eng, "_resolve_step_card",
                                   return_value={"id": "t_w", "status": "ready"}):
                eng.run()
            self.assertEqual(self._load(eng)["step"], "ww_write")
            self.assertEqual(mk_reopen.call_count, 1)
            # 拍3：新一轮 done → 放行推进
            eng.run()
        self.assertEqual(self._load(eng)["step"], "ww_polish")
        self.assertEqual(mk_reopen.call_count, 1)

    def test_reopen_failure_retries_next_beat(self):
        """重开失败：留步且不打派发标记 → 下一拍继续重试（不留死角）。"""
        eng = self._eng()
        self._state(eng, "ww_write")
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_w", "status": "done"}), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=False) as mk:
            out = eng.run()
            st = self._load(eng)
            self.assertEqual(st["step"], "ww_write")
            self.assertEqual(st["dispatched"], {})  # 未打标记
            self.assertIn("重开失败", out)
            eng.run()
            self.assertEqual(mk.call_count, 2)  # 下一拍重试

    # ---- 并行组：成员卡全 done 才放行 ----
    def test_group_stays_until_all_members_done(self):
        eng = self._eng()
        self._state(eng, "ww_mat_group")
        created = []
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card",
                               side_effect=lambda s, st, it: created.append(s) or f"t_{s}"):
            out = eng.run()  # 拍1：5 卡建好，留本步
        self.assertEqual(len(created), 5)
        self.assertEqual(len(set(created)), 5)  # 幂等键各含 member id
        self.assertEqual(self._load(eng)["step"], "ww_mat_group")
        self.assertIn("留本步等待", out)

        partial = {m: {"id": f"t_{m}", "status": "done"} for m in MAT_MEMBERS}
        partial["ww_mat_econ"] = {"id": "t_econ", "status": "running"}
        with mock.patch.object(eng, "_resolve_step_card",
                               side_effect=lambda sid: partial.get(sid)), \
             mock.patch.object(eng, "_create_card", return_value="t_nope") as mk:
            eng.run()  # 拍2：4 done + 1 running → 仍留步
        self.assertEqual(self._load(eng)["step"], "ww_mat_group")
        mk.assert_not_called()  # done 成员不重派

        all_done = {m: {"id": f"t_{m}", "status": "done"} for m in MAT_MEMBERS}
        with mock.patch.object(eng, "_resolve_step_card",
                               side_effect=lambda sid: all_done.get(sid)):
            out = eng.run()  # 拍3：全 done → 放行到屏障
        self.assertEqual(self._load(eng)["step"], "ww_mat_barrier")
        self.assertIn("全 done", out)

    # ---- 断点恢复（charter S4）----
    def test_interrupt_resume_does_not_redo_done_step(self):
        """中断前已派卡并落盘标记，重启后卡 done → 直接推进，不重做已完成步骤。"""
        eng = self._eng("test-resume")
        self._state(eng, "ww_mat_select")
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_ms"):
            eng.run()  # 建卡后落盘 —— 此后进程被 kill
        eng2 = self._eng("test-resume")  # 重启：新对象，只从实例 JSON 恢复
        self.assertEqual(eng2._load_state()["step"], "ww_mat_select")  # 断点合法
        with mock.patch.object(eng2, "_resolve_step_card",
                               return_value={"id": "t_ms", "status": "done"}), \
             mock.patch.object(eng2, "_create_card", return_value="t_again") as mk, \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen:
            eng2.run()
        self.assertEqual(self._load(eng2)["step"], "ww_write")
        mk.assert_not_called()  # 不重派
        mk_reopen.assert_not_called()  # 不当成回流重做

    def test_resume_in_place_keeps_marker_jump_clears_it(self):
        """原地续跑（--step 当前步）保留标记；跳到别的步清标记（防 done 卡被误放行）。"""
        eng = self._eng()
        self._state(eng, "ww_write", iteration=2, dispatched={"ww_write": 2})
        done = {"id": "t_w", "status": "done"}
        with mock.patch.object(eng, "_resolve_step_card", return_value=done), \
             mock.patch.object(eng, "_create_card", return_value="t_w"), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen:
            eng.run(target_step="ww_write")  # 原地续跑 → done 视为本轮交付
        self.assertEqual(self._load(eng)["step"], "ww_polish")
        mk_reopen.assert_not_called()
        # 再进 ww_write（等价拍板打回路径）：标记已随推进清空 → done 卡重开重做
        with mock.patch.object(eng, "_resolve_step_card", return_value=done), \
             mock.patch.object(eng, "_create_card", return_value="t_w"), \
             mock.patch.object(ww_engine.kanban_client, "reopen_done_card",
                               return_value=True) as mk_reopen2:
            out = eng.run(target_step="ww_write")
        self.assertEqual(self._load(eng)["step"], "ww_write")
        mk_reopen2.assert_called_once()
        self.assertIn("重开重做", out)

    def test_legacy_state_without_dispatched_key(self):
        """B2 前落盘的实例 JSON 无 dispatched 键 → 自动补齐，不 KeyError。"""
        eng = self._eng("test-legacy")
        eng.instance_path.write_text(json.dumps(
            {"instance": "test-legacy", "step": "ww_topic", "iteration": 1,
             "history": [], "status": "running"}), encoding="utf-8")
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_1", "status": "running"}), \
             mock.patch.object(eng, "_create_card", return_value="t_nope") as mk:
            eng.run()
        st = self._load(eng)
        self.assertEqual(st["step"], "ww_topic")
        self.assertEqual(st["dispatched"], {"ww_topic": 1})
        self.assertEqual(st["iteration"], 1)  # 在途轮询不自增
        mk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
