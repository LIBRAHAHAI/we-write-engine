"""T2 状态机测试：单步推进 / decision 消费 / dispatch_group / 断点恢复。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
import yaml
from engine import WeWriteEngine

# t_a6da5fa1：引擎新增「正文非空」硬门（空壳产物视同未交付）——屏障用例的夹具
# 产物必须具备真实正文（旧夹具正文仅 "t" 会被硬门拒收，属夹具失真非代码回归）。
_MAT_BODY = ("---\nstatus: done\n---\n\n# 素材夹具\n\n"
             "本行正文用于验证正文非空硬门对正常产物放行（sandbox 用例专用）。" * 8 + "\n")  # 016 #2 阈值 200


class _TmpEngine(WeWriteEngine):
    """用临时实例目录的引擎（不碰真实 instances/）。"""

    def __init__(self, tmpdir: Path, sandbox_artifacts: Path | None = None):
        self.instance = "test-case"
        self.mode = "test"  # M3/Δ10：_step_goal/_do_dispatch 消费
        self.template = yaml.safe_load(
            (Path(__file__).parent.parent / "config" / "templates" / "hot-topic-commentary.yaml").read_text(encoding="utf-8"))
        # 0915 假红根治：pipeline yaml 内写死的真实池 glob 重定向到 sandbox
        # （09-14 清理后真实池已回填 article-002 五路，硬编码用例的假产物会与真实
        # 五路撞 glob → 假绿/假红洗牌）。同 test_parallel_group 方案 A。
        if sandbox_artifacts is not None:
            real_prefix = "./exchange/artifacts/"
            sandbox_prefix = str(sandbox_artifacts).replace("\\", "/") + "/"
            def _rewrite(obj):
                if isinstance(obj, str):
                    return obj.replace(real_prefix, sandbox_prefix)
                if isinstance(obj, list):
                    return [_rewrite(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _rewrite(v) for k, v in obj.items()}
                return obj
            self.template = _rewrite(self.template)
        self.instance_path = tmpdir / "test-case.json"


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        ww_engine.INSTANCES_DIR = self.tmp
        self.sandbox_artifacts = self.tmp / "artifacts"
        self.sandbox_artifacts.mkdir(parents=True, exist_ok=True)

    def _mat(self, area: str, prefix: str = "000") -> Path:
        return self.sandbox_artifacts / f"{prefix}-hermes-ww-mat-{area}-test.md"

    def _eng(self, sandbox_artifacts: Path | None = None) -> _TmpEngine:
        return _TmpEngine(self.tmp, sandbox_artifacts=sandbox_artifacts)

    def _dispatch_done_beats(self, eng, card_id: str = "t_fake") -> str:
        """B2 两拍语义辅助：拍1 建卡（留本步）→ 拍2 卡 done（推进 next）。

        2026-09-11 B2 修复前 dispatch 建卡即推进，老用例一拍就过门；修复后必须
        模拟 worker 把卡做完引擎才推进。改这些用例是新语义的直接后果（断言意图
        未放宽：仍然验证"该步执行完落到下一步"）。
        """
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value=card_id):
            eng.run()
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": card_id, "status": "done"}):
            return eng.run()

    def test_first_run_starts_running(self):
        eng = self._eng()
        # B2 断言变更：首跑建卡后**留本步** ww_topic（卡 done 才推进门1）。
        # 同时补 mock：原用例裸跑真 kanban，每次 pytest 都会 reopen 真实卡
        # t_d35fcbbd 并触发 dispatcher spawn（泄漏根治，对齐子卡 t_8f01dc6d）。
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_fake") as mk:
            out = eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["status"], "running")
        self.assertEqual(st["step"], "ww_topic")
        self.assertIn("建卡 t_fake", out)
        self.assertIn("卡在途，留本步等待", out)
        mk.assert_called_once()

    def test_wait_blocks_until_all_artifacts(self):
        eng = self._eng()
        # mock 建卡（不真写 kanban）；B2 两拍后 ww_topic 卡 done → 落 ww_gate_topic
        self._dispatch_done_beats(eng)
        # D1（20260920）：门1 已声明保守默认 A → 无 decision 文件时自动拍
        # （20260921 修正：自动拍需 opt-in，此处勾选「无人值守」模拟授权）
        (self.tmp / "test-case.unattended").write_text("", encoding="utf-8")
        out = eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_outline")
        self.assertIn("拍板(预设默认)", out)
        # 手动拍板文件消费语义改在永不默认的收官门验证（D1：user 专属门阻塞等）：
        # 无文件 → 停在拍板点；写 A → 消费推进且文件删除
        eng._save_state({"instance": "test-case", "step": "ww_closing_gate",
                         "iteration": 5, "history": [], "status": "running",
                         "dispatched": {}})
        out = eng.run()
        self.assertIn("拍板点", out)
        (self.tmp / "test-case.decision").write_text("A", encoding="utf-8")
        out = eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_done")
        self.assertIn("拍板", out)
        # decision 文件已消费删除
        self.assertFalse((self.tmp / "test-case.decision").exists())

    def test_decision_reject_goes_on_reject(self):
        eng = self._eng()
        self._dispatch_done_beats(eng)  # ww_topic 卡 done → 落门1
        (self.tmp / "test-case.decision").write_text("重选", encoding="utf-8")
        eng.run()  # 消费 decision → step=ww_topic（单步语义，dispatch 下一拍执行）
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_topic")  # on_reject 回选题
        # 下一拍：重派选题卡（B2：建卡后留本步，卡 done 才再推进）
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_fake2") as mk:
            eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_topic")  # B2：留本步等卡 done
        mk.assert_called_once()  # 重选后重新派卡
        with mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_fake2", "status": "done"}):
            eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_gate_topic")

    def test_dispatch_group_creates_member_cards(self):
        """并行组：5 成员各建一卡（mock 建卡，验证调用次数与幂等键独立）。

        B2：建卡后留本步，成员卡全 done 才放行到屏障。
        """
        eng = self._eng()
        # 直接到并行组步骤
        st = {"instance": "test-case", "step": "ww_mat_group", "iteration": 0,
              "history": [], "status": "running"}
        eng._save_state(st)
        created = []

        def fake_create(step_id, step, iteration):
            created.append(step_id)
            return f"t_fake{len(created)}"

        with mock.patch.object(eng, "_create_card", side_effect=fake_create), \
             mock.patch.object(eng, "_resolve_step_card", return_value=None):
            out = eng.run()
        self.assertEqual(len(created), 5)
        self.assertEqual(len(set(created)), 5)
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_mat_group")  # B2：组卡未 done，留本步
        self.assertIn("留本步等待", out)
        # 成员卡全 done → 放行，且不再重复建卡
        with mock.patch.object(eng, "_create_card", side_effect=fake_create), \
             mock.patch.object(eng, "_resolve_step_card",
                               return_value={"id": "t_x", "status": "done"}):
            eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_mat_barrier")
        self.assertEqual(len(created), 5)

    def test_barrier_waits_and_escalates(self):
        # 0915 假红根治：_eng() 走 sandbox glob（见 _TmpEngine），产物写 sandbox 池
        eng = self._eng(sandbox_artifacts=self.sandbox_artifacts)
        st = {"instance": "test-case", "step": "ww_mat_barrier", "iteration": 1,
              "history": [], "status": "running"}
        eng._save_state(st)
        # 5 轮缺失 → escalate
        for _ in range(5):
            eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_mat_barrier")  # 不放行
        self.assertIn("wait_escalate", [h.get("action") for h in st["history"]])
        # 产物全齐 → 放行（文件名须匹配 sandbox 内 pipeline glob：*-ww-mat-<路>*.md）
        arts = ["news", "history", "classic", "law", "econ"]
        for a in arts:
            self._mat(a).write_text(_MAT_BODY, encoding="utf-8")
        eng.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_mat_select")

    def test_resume_from_step(self):
        """断点恢复：--step 指定续跑（S4）。B2 后重启实例不重做已完成步骤。"""
        eng = self._eng()
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card", return_value="t_fake"):
            eng.run(target_step="ww_mat_select")
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["status"], "running")
        self.assertEqual(st["step"], "ww_mat_select")  # B2：建卡后留本步等卡 done
        # 进程中断重启：新引擎对象只从实例 JSON 恢复，卡已 done → 推进且不重派
        eng2 = self._eng()
        with mock.patch.object(eng2, "_resolve_step_card",
                               return_value={"id": "t_fake", "status": "done"}), \
             mock.patch.object(eng2, "_create_card", return_value="t_redo") as mk2:
            eng2.run()
        st = json.loads(eng.instance_path.read_text(encoding="utf-8"))
        self.assertEqual(st["step"], "ww_write")  # 从 ww_mat_select 续跑后落 ww_write
        mk2.assert_not_called()  # 不重做已完成步骤

    # ================= 并自用例（原仓库根 tests/test_state_machine.py，D3 收敛）==========
    # 根侧 8 例中 4 例与本文件上方用例同意图且上方断言更强（首跑留步 / 门1 消费拍板 /
    # 拍板打回 / 并行组建卡），未重复搬运；以下 4 例是根侧独有覆盖。
    # 涉及 dispatch 的按 B2 补第二拍（建卡留步 → 卡 done 才推进），断言意图未放宽。

    def _st(self, eng) -> dict:
        return json.loads(eng.instance_path.read_text(encoding="utf-8"))

    def _write_decision(self, eng, text: str) -> None:
        (self.tmp / f"{eng.instance}.decision").write_text(text, encoding="utf-8")

    def _group_done_beats(self, eng) -> str:
        """dispatch_group 两拍：拍1 建齐成员卡（留本步）→ 拍2 全 done（放行到屏障）。"""
        with mock.patch.object(eng, "_resolve_step_card", return_value=None), \
             mock.patch.object(eng, "_create_card",
                               side_effect=lambda s, st, it: f"t_{s}"):
            eng.run()
        with mock.patch.object(eng, "_resolve_step_card",
                               side_effect=lambda sid: {"id": f"t_{sid}", "status": "done"}):
            return eng.run()

    def test_barrier_blocks_until_artifacts(self):
        """屏障缺产物不放行 + schema 声明 5 路产物（根侧独有：产物条数断言）。

        屏障 glob 打的是 pipeline.yaml 里写死的真实 exchange/artifacts/，而那里已有
        006-default-ww-mat-history.md / 012-jingwei-ww-mat-news.md 两份真产物——直接跑
        真实目录会让本例结果随 exchange/ 内容漂移（五路补齐那天就变红）。故"不放行"
        这半段的产物目录改指 tmp；schema 条数断言仍打真实 pipeline.yaml。
        """
        eng = self._eng()
        barrier = eng._step_by_id("ww_mat_barrier")
        self.assertEqual(len(barrier["artifacts"]), 5)  # schema 断言：真实 pipeline
        barrier["artifacts"] = [str(self.tmp / "art" / f"*-{n}*.md")
                                for n in ("news", "history", "classic", "law", "econ")]
        eng._save_state({"instance": "test-case", "step": "ww_mat_barrier", "iteration": 1,
                         "history": [], "status": "running", "dispatched": {}})
        out = eng.run()
        self.assertTrue("等待" in out or "⏳" in out, f"屏障缺产物应等待，实际: {out!r}")
        self.assertEqual(self._st(eng)["step"], "ww_mat_barrier")  # 不放行

    def test_decision_pending_waits(self):
        """D1（20260920）：已声明默认的门自动拍；永不默认的收官门仍停拍板点等 user。"""
        eng = self._eng()
        self._dispatch_done_beats(eng)  # ww_topic 建卡 → 卡 done → 落门1
        self.assertEqual(self._st(eng)["step"], "ww_gate_topic")
        (self.tmp / "test-case.unattended").write_text("", encoding="utf-8")  # opt-in
        out = eng.run()  # 不写 decision 文件 → D1 默认 A 自动拍
        self.assertIn("拍板(预设默认)", out)
        # 收官门（user 专属，永不默认）无文件 → 停在拍板点
        eng._save_state({"instance": "test-case", "step": "ww_closing_gate",
                         "iteration": 9, "history": [], "status": "running",
                         "dispatched": {}})
        out = eng.run()
        self.assertIn("拍板点", out)
        self.assertEqual(self._st(eng)["step"], "ww_closing_gate")  # 停住不动

    def test_terminal_state_stops(self):
        """终态实例不再推进（根侧独有；不涉 dispatch，原样并入）。"""
        eng = self._eng()
        eng._save_state({"instance": "test-case", "step": "ww_done", "iteration": 9,
                         "history": [], "status": "completed", "dispatched": {}})
        out = eng.run()
        self.assertIn("终态", out)
        self.assertEqual(self._st(eng)["step"], "ww_done")  # 不得乱走

    def test_full_chain_resume_through_gates(self):
        """全链走查：选题→门1→大纲→门2→五路组→屏障，kill 后新对象从 JSON 续跑。

        根侧 test_resume_from_json_step 并入：与上方 test_resume_from_step（--step 跳步）
        互补，本例走**真实门链**（连过两道 decision 门 + 一个并行组）。
        每个 dispatch 步按 B2 补第二拍。
        """
        eng = self._eng()
        self._dispatch_done_beats(eng)                    # ww_topic → 门1
        self.assertEqual(self._st(eng)["step"], "ww_gate_topic")
        self._write_decision(eng, "A")
        out = eng.run()                                   # 门1 消费 → ww_outline
        self.assertIn("ww_outline", out)
        self.assertFalse((self.tmp / "test-case.decision").exists())  # 拍板文件已消费

        self._dispatch_done_beats(eng)                    # ww_outline → 门2
        self.assertEqual(self._st(eng)["step"], "ww_gate_outline")
        self._write_decision(eng, "同意")
        out = eng.run()                                   # 门2 消费 → ww_mat_group
        self.assertIn("ww_mat_group", out)

        self._group_done_beats(eng)                       # 5 卡建齐 → 全 done → 屏障
        self.assertEqual(self._st(eng)["step"], "ww_mat_barrier")

        # 模拟 kill 重启：全新引擎对象只从实例 JSON 恢复，从屏障续跑
        eng2 = self._eng()
        self.assertEqual(eng2._load_state()["step"], "ww_mat_barrier")
        self.assertTrue(eng2.run())                       # 不抛异常即续跑成功


if __name__ == "__main__":
    unittest.main()
