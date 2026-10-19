"""020 模型路由补链路测试：dispatch 注入卡 override + ensure 回填 + 文案修正。

覆盖（020 §3 验证计划）：
  1. _create_card 建卡命令行含 --model/--provider 且值 == resolve_routing(step)
  2. ensure_card_model：双 NULL 回填 / 已有 pin 不覆盖
  3. _step_goal 文案「路由声明已注入卡字段」，不再出现「实际调用」
  4. _do_dispatch 在途卡路径触发回填（mock _resolve_step_card 返回在途卡）

实弹纪律：全部 mock subprocess（不真建卡）/ tmp sqlite（不触生产板）。

t_2c740cd9（20260926）：引擎写侧新增「test 模式拒建卡」fail-closed 门，本类用例
直调 `_create_card`（mode 缺省 test）需显式 opt-in 才走到命令行断言——用环境变量
WW_ENGINE_TEST_DISPATCH=1（同 mock subprocess 口径，仍零活跃板接触）。
"""
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))
import kanban_client
import model_router


def _tmp_db(tmp_path: Path) -> Path:
    """一次性 kanban DB（schema 最小集：tasks + task_events）。"""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'ready',
            model_override TEXT, provider_override TEXT,
            idempotency_key TEXT, created_at INTEGER, completed_at INTEGER
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER
        );
    """)
    conn.execute("INSERT INTO tasks (id, title, status) VALUES ('t_test01', 'x', 'ready')")
    conn.commit()
    conn.close()
    return db


class CreateCardInjectsOverrideTest(unittest.TestCase):
    """断言 1：建卡命令行含 --model/--provider 且等于 resolve_routing 值。"""

    def test_create_card_appends_model_provider(self):
        import engine as eng_mod
        e = eng_mod.WeWriteEngine("t-route01")
        step = next(s for s in e.template["steps"] if s["id"] == "ww_write")
        fr, _ = model_router.resolve_routing("ww_write")
        self.assertIsNotNone(fr)  # 模板 models 段声明了 ww_write
        captured = {}

        def fake_run(cmd, **kw):
            captured["argv"] = cmd
            class R:  # noqa: N801
                returncode = 0
                stdout = "t_abc123de"  # 纯 hex：引擎用 t_[0-9a-f]+ 提取卡 id
                stderr = ""
            return R()

        with mock.patch.object(eng_mod.subprocess, "run", side_effect=fake_run):
            with mock.patch.dict(os.environ, {eng_mod.TEST_DISPATCH_OPTIN_ENV: "1"}):
                card_id = e._create_card("ww_write", step, 1)
        argv = captured["argv"]
        self.assertEqual(card_id, "t_abc123de")
        self.assertIn("--model", argv)
        self.assertIn("--provider", argv)
        self.assertEqual(argv[argv.index("--model") + 1], fr["model"])
        self.assertEqual(argv[argv.index("--provider") + 1], fr["provider"])

    def test_create_card_unrouted_step_no_flags(self):
        """无路由声明步骤（models 段无条目）不加 --model/--provider。"""
        import engine as eng_mod
        e = eng_mod.WeWriteEngine("t-route02")
        step = {"id": "ww_nonexistent_step_xyz", "description": "x"}
        captured = {}

        def fake_run(cmd, **kw):
            captured["argv"] = cmd
            class R:  # noqa: N801
                returncode = 0
                stdout = "t_fakeroute2"
                stderr = ""
            return R()

        with mock.patch.object(eng_mod.subprocess, "run", side_effect=fake_run):
            with mock.patch.dict(os.environ, {eng_mod.TEST_DISPATCH_OPTIN_ENV: "1"}):
                e._create_card("ww_nonexistent_step_xyz", step, 1)
        self.assertNotIn("--model", captured["argv"])
        self.assertNotIn("--provider", captured["argv"])


class EnsureCardModelTest(unittest.TestCase):
    """断言 2：双 NULL 回填；已有 pin 不覆盖。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _tmp_db(Path(self._tmp.name))
        self._prev = None

    def tearDown(self):
        self._tmp.cleanup()

    def test_backfills_when_both_null(self):
        with mock.patch.object(kanban_client, "resolve_db_path", return_value=self.db):
            ok = kanban_client.ensure_card_model("t_test01", "deepseek", "deepseek-v4-pro")
        self.assertTrue(ok)
        conn = sqlite3.connect(str(self.db))
        row = conn.execute(
            "SELECT model_override, provider_override FROM tasks WHERE id='t_test01'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("deepseek-v4-pro", "deepseek"))

    def test_does_not_overwrite_manual_pin(self):
        conn = sqlite3.connect(str(self.db))
        conn.execute("UPDATE tasks SET model_override='glm', provider_override='zai' "
                     "WHERE id='t_test01'")
        conn.commit()
        conn.close()
        with mock.patch.object(kanban_client, "resolve_db_path", return_value=self.db):
            ok = kanban_client.ensure_card_model("t_test01", "qwen", "qwen3.8-max")
        self.assertFalse(ok)  # 人工 pin 不被覆盖
        conn = sqlite3.connect(str(self.db))
        row = conn.execute(
            "SELECT model_override, provider_override FROM tasks WHERE id='t_test01'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("glm", "zai"))


class StepGoalWordingTest(unittest.TestCase):
    """断言 3：文案降级为「路由声明已注入卡字段」，不再自称运行事实。"""

    def test_goal_says_declared_not_actual(self):
        import engine as eng_mod
        e = eng_mod.WeWriteEngine("t-route03")
        step = next(s for s in e.template["steps"] if s["id"] == "ww_write")
        body = e._step_goal(step)
        self.assertIn("路由声明已注入卡字段", body)
        # 020 边界：引擎自述不再自称「实际调用」；model_router 日志行
        # 「→ 实际调用 provider=...」是路由声明的一部分，不在本卡改动范围。
        self.assertNotIn("本卡实际调用", body)
        self.assertNotIn("（模型路由：本卡实际调用", body)


class DispatchBackfillTest(unittest.TestCase):
    """断言 4：在途卡路径触发 ensure_card_model 回填。"""

    def test_inflight_card_gets_backfill(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        try:
            db = _tmp_db(Path(tmp.name))
            import engine as eng_mod
            e = eng_mod.WeWriteEngine("t-route04")
            step = next(s for s in e.template["steps"] if s["id"] == "ww_write")
            fr, _ = model_router.resolve_routing("ww_write")
            inflight = {"id": "t_test01", "status": "ready"}
            with mock.patch.object(eng_mod.subprocess, "run"), \
                 mock.patch.object(e, "_resolve_step_card", return_value=inflight), \
                 mock.patch.object(kanban_client, "resolve_db_path", return_value=db), \
                 mock.patch.object(kanban_client, "ensure_card_model",
                                   return_value=True) as m_ensure:
                e._do_dispatch(step, {"iteration": 1, "history": [], "dispatched": {}})
            m_ensure.assert_called_once_with("t_test01", fr["provider"], fr["model"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
