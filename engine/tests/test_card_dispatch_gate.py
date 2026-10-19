"""t_2c740cd9 回归：test 模式建卡 fail-closed 门（幻影卡零泄漏）。

缺陷实证（2026-09-26 22:01:40）：pytest 用例
`test_skeleton_refuses_run_but_status_ok` 以 `WeWriteEngine("t-skel",
template="novel-short")`（mode 缺省 test、state 落一次性临时目录）实跑；当时
working tree 里 novel-short 已去 skeleton，run() 未被拒 → `_create_card` 真发
`hermes kanban create` 命中活跃板，留下孤儿幻影卡 t_773acac9。

修复语义（引擎写侧）：
  - `_create_card`：mode != live 且无显式 opt-in → raise（绝不 subprocess 建卡）；
  - opt-in 两道口子：实例 state `allow_test_dispatch: true` / 环境变量
    `WW_ENGINE_TEST_DISPATCH=1`（真实实例目录内的演练实例用前者，如
    engine/instances/novel-pilot.json）；
  - live 模式不受影响（无需 opt-in）。

⚠️ 实弹纪律（conftest）：本用例全部落一次性 temp；subprocess.run 全程 mock 并
断言「活跃板零接触」（calls 为空 / 无 kanban create 命令）。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine as ww_engine
from engine import WeWriteEngine

STEP = {"id": "ns_premise", "type": ww_engine.T_DISPATCH, "assignee": "duanmu",
        "description": "占位步骤", "next": "ns_done"}
FAKE_CARD_ID = "t_deadbeef"


class _FakeCompleted:
    returncode = 0
    stdout = f"created {FAKE_CARD_ID}\n"
    stderr = ""


def _recorder(calls):
    def _run(cmd, *a, **kw):
        calls.append(cmd)
        return _FakeCompleted()

    return _run


def _kanban_creates(calls):
    return [c for c in calls if "kanban" in c and "create" in c]


class TestModeDispatchGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ww_dispatch_gate_"))
        self._env_bak = os.environ.pop(ww_engine.TEST_DISPATCH_OPTIN_ENV, None)

    def tearDown(self):
        os.environ.pop(ww_engine.TEST_DISPATCH_OPTIN_ENV, None)
        if self._env_bak is not None:
            os.environ[ww_engine.TEST_DISPATCH_OPTIN_ENV] = self._env_bak

    def _engine(self, instance: str, mode, state: dict | None = None) -> WeWriteEngine:
        eng = WeWriteEngine.__new__(WeWriteEngine)
        eng.instance = instance
        eng.mode = mode
        eng.template = {"steps": [STEP], "skills_map": {}}  # _step_goal 需要
        eng.instance_path = self.tmp / f"{instance}.json"
        if state is not None:
            eng.instance_path.write_text(json.dumps(state), encoding="utf-8")
        return eng

    # ---- 判据：默认拒绝 ----

    def test_test_mode_without_optin_refuses(self):
        """mode=test + 无 opt-in → raise，且 subprocess 零调用（活跃板零接触）。"""
        eng = self._engine("t-ghost", "test")
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            with self.assertRaises(RuntimeError) as cm:
                eng._create_card("ns_premise", STEP, 1)
        msg = str(cm.exception)
        self.assertIn("拒建卡", msg)
        self.assertIn("t_2c740cd9", msg)
        self.assertIn("t-ghost", msg)
        self.assertEqual(calls, [])
        self.assertEqual(_kanban_creates(calls), [])

    def test_mode_none_treated_as_test_refuses(self):
        """mode=None（未定）按防呆方向视同 test → 拒。"""
        eng = self._engine("t-nomode", None)
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            with self.assertRaises(RuntimeError):
                eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(calls, [])

    def test_broken_state_json_refuses(self):
        """state 坏 JSON 读不到 allow_test_dispatch → 拒绝（不可判定不倒向放行）。"""
        eng = self._engine("t-broken", "test")
        eng.instance_path.write_text("{ not json", encoding="utf-8")
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            with self.assertRaises(RuntimeError):
                eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(calls, [])

    # ---- 放行口 ----

    def test_state_optin_allows(self):
        """实例 state allow_test_dispatch: true → 放行（走 subprocess）。"""
        eng = self._engine("novel-pilot-like", "test",
                           {"instance": "novel-pilot-like", "mode": "test",
                            "allow_test_dispatch": True})
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            card = eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(card, FAKE_CARD_ID)
        self.assertEqual(len(_kanban_creates(calls)), 1)

    def test_state_optin_must_be_true(self):
        """allow_test_dispatch 非 True（如字符串 "1"）→ 仍拒（严格 is True）。"""
        eng = self._engine("t-loose", "test",
                           {"mode": "test", "allow_test_dispatch": "1"})
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            with self.assertRaises(RuntimeError):
                eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(calls, [])

    def test_env_optin_allows(self):
        """环境变量 WW_ENGINE_TEST_DISPATCH=1 → 放行（临时手工演练）。"""
        os.environ[ww_engine.TEST_DISPATCH_OPTIN_ENV] = "1"
        eng = self._engine("t-manual-drill", "test")
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            card = eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(card, FAKE_CARD_ID)
        self.assertEqual(len(_kanban_creates(calls)), 1)

    def test_live_mode_allows_without_optin(self):
        """live 模式不受门影响（无需 opt-in）。"""
        eng = self._engine("article-005-like", "live")
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)):
            card = eng._create_card("ns_premise", STEP, 1)
        self.assertEqual(card, FAKE_CARD_ID)
        self.assertEqual(len(_kanban_creates(calls)), 1)

    # ---- 端到端：泄漏现场复现（改前会建卡 / 改后被门拒）----

    def test_leak_scenario_t_skel_full_run_refused(self):
        """t-skel × novel-short 全链实跑复现：run() 必须 raise 且零 kanban create。

        改前（22:01:40 实测）：run() 推进 ns_premise → 建卡 t_773acac9。
        改后：_do_dispatch → _create_card → fail-closed 门 raise。
        """
        calls = []
        with mock.patch.object(ww_engine.subprocess, "run", side_effect=_recorder(calls)), \
             mock.patch.object(WeWriteEngine, "_resolve_step_card", return_value=None):
            eng = WeWriteEngine("t-skel-gate", template="novel-short")
            with self.assertRaises(RuntimeError) as cm:
                eng.run()
        self.assertIn("拒建卡", str(cm.exception))
        self.assertEqual(_kanban_creates(calls), [],
                         f"门未拦住，向活跃板发了建卡命令: {_kanban_creates(calls)}")


if __name__ == "__main__":
    unittest.main()
