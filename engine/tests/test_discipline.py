"""T5 纪律校验回归：违规样本 100% 拦截 + 干净稿零误报。

2026-09-11 D3 收敛：原仓库根 tests/test_discipline.py 的 fixtures 通配（数据驱动）
用例已并入本文件末段，两份 fixtures 样本合并到 tests/fixtures/。
"""
import sys
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import discipline

FIXTURES = Path(__file__).parent / "fixtures"


class DisciplineTest(unittest.TestCase):
    def _clean(self) -> str:
        return (FIXTURES / "sample_clean.md").read_text(encoding="utf-8")

    def _viol(self) -> str:
        return (FIXTURES / "sample_violation_agent.md").read_text(encoding="utf-8")

    # ---- 干净稿零误报 ----
    def test_clean_zero_false_positive(self):
        r = discipline.postcheck(self._clean())
        self.assertTrue(r["ok"], f"干净稿误报: {r['violations']}")

    def test_clean_precheck_ok(self):
        r = discipline.precheck(self._clean())
        self.assertTrue(r["ok"])

    # ---- 违规样本 100% 拦截 ----
    def test_agent_signature_caught(self):
        r = discipline.postcheck(self._viol())
        self.assertFalse(r["ok"])
        self.assertTrue(any("agent署名" in v for v in r["violations"]))

    def test_meta_discourse_caught(self):
        r = discipline.postcheck("根据我们的定位，本文遵循既定风格要求。" + "字" * 1300)
        self.assertFalse(r["ok"])
        self.assertTrue(any("定位元话语" in v for v in r["violations"]))

    def test_process_meta_caught(self):
        r = discipline.postcheck("以下是为你撰写的文章。" + "字" * 1300)
        self.assertFalse(r["ok"])

    def test_min_chars_caught(self):
        r = discipline.postcheck("这是一篇太短的文章。")
        self.assertFalse(r["ok"])
        self.assertTrue(any("字数下限" in v for v in r["violations"]))

    def test_precheck_forbidden_term(self):
        r = discipline.precheck("素材里提到赌博网站的内容")
        self.assertFalse(r["ok"])
        self.assertTrue(any("赌博网站" in v for v in r["violations"]))

    def test_each_rule_has_red_case(self):
        """规则集每条 forbidden_pattern 至少被一个红用例覆盖（防规则死码）。"""
        rules = discipline.load_rules()
        samples = {
            "agent署名": self._viol(),
            "定位元话语": "根据我们的定位写作。" + "字" * 1300,
            "流程元话语": "以下是为你撰写的文章。" + "字" * 1300,
        }
        for rule in rules.get("forbidden_patterns") or []:
            name = rule.get("name")
            self.assertIn(name, samples, f"规则 {name} 无红用例")
            r = discipline.postcheck(samples[name])
            self.assertFalse(r["ok"], f"规则 {name} 红用例未拦截")


# ============ 并自用例（原仓库根 tests/test_discipline.py，D3 收敛 2026-09-11）============
# 根侧是 fixtures 通配的数据驱动写法，与上方定点样本互补：往 fixtures/ 丢新样本即自动
# 纳入回归，无需改代码。函数名加 fixtures_ 以区别上方同意图的定点用例（断言原样保留）。

VIOLATION_SAMPLES = sorted(FIXTURES.glob("VIOLATION_*.txt"))
CLEAN_SAMPLES = sorted(FIXTURES.glob("CLEAN_*.txt"))


def test_fixtures_exist():
    assert VIOLATION_SAMPLES, "缺违规样本 fixtures"
    assert CLEAN_SAMPLES, "缺干净稿 fixtures"


@pytest.mark.parametrize("fp", VIOLATION_SAMPLES, ids=lambda p: p.stem)
def test_violation_fixtures_intercepted(fp):
    res = discipline.postcheck(fp.read_text(encoding="utf-8"))
    assert not res["ok"], f"违规样本 {fp.name} 漏拦: {res['violations']}"
    assert res["violations"], "拦截但未给出违规项"


@pytest.mark.parametrize("fp", CLEAN_SAMPLES, ids=lambda p: p.stem)
def test_clean_fixtures_zero_false_positive(fp):
    # 字数下限单测覆盖（test_min_chars_enforced）；干净稿回归聚焦正则误报
    res = discipline.postcheck(fp.read_text(encoding="utf-8"), min_chars=100)
    assert res["ok"], f"干净稿 {fp.name} 误报: {res['violations']}"


def test_precheck_blocks_forbidden_terms():
    res = discipline.precheck("今天聊聊赌博网站的套路")
    assert not res["ok"]


def test_precheck_clean_pass():
    assert discipline.precheck("聊聊三国里的用人之道")["ok"]


def test_min_chars_enforced():
    res = discipline.postcheck("太短的稿子")
    assert not res["ok"]
    assert any("字数下限" in v for v in res["violations"])


if __name__ == "__main__":
    unittest.main()
