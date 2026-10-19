"""discipline.py — 写作纪律机器校验（T5，charter S3；M2 扩 013 Δ4 wordlist）。

规则类型（单一事实源 discipline_rules.yaml）：
  1. precheck_terms：写前检查——输入素材不含越界词
  2. forbidden_patterns：审后正则——agent 署名、定位元话语、字数下限等（v1）
  3. wordlist（M2/Δ4）：L1' 引擎补充词库扫描——广告法/医疗/金融/诈骗四类
     （wordscheck 零覆盖）。词条精确/包含匹配，非正则；支持 `词条|豁免上下文`
     语法（如 `第一|第一时间` 表示"第一时间"豁免）。

L1' 定位诚实声明（013 §4.1）：补充词库只解决「明文违规用语」这一层，谐音/变体
仍归玄武 L2 语义审——三层分工不越位。

验收硬标准（charter S3）：机器可查项 100% 拦截、干净稿零误报。
规则集/词库改动必跑样本稿回归集（tests/fixtures/ + test_discipline.py）。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ENGINE_DIR = Path(__file__).parent
RULES_PATH = ENGINE_DIR / "discipline_rules.yaml"
WORDLIST_DIR = ENGINE_DIR / "config" / "wordlists"


def load_rules() -> dict:
    if not RULES_PATH.exists():
        return {"forbidden_patterns": [], "wordlist_rules": [], "precheck_terms": [],
                "min_chars": 1200}
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}


def load_wordlist(path: str | Path) -> list[tuple[str, str | None]]:
    """词库文件 → [(词条, 豁免上下文|None)]。一行一词条，# 注释，`词|豁免` 语法。"""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            term, exempt = line.split("|", 1)
            out.append((term.strip(), exempt.strip() or None))
        else:
            out.append((line, None))
    return out


def _check_wordlist(text: str, rule: dict) -> list[str]:
    """wordlist 规则：逐词条包含匹配 + 豁免上下文判负。返回违规消息列表。

    G4/023：severity=warn 时消息前缀改为「warn:」——调用方（postcheck）按前缀
    分流到 warns，不参与 ok 判定（013 R6 兜底：词库先 warn 试运行再升 block）。
    """
    violations = []
    wl_path = WORDLIST_DIR / rule.get("wordlist", "")
    name = rule.get("name", wl_path.stem or "wordlist")
    severity = rule.get("severity", "block")
    prefix = "postcheck-warn" if severity == "warn" else "postcheck"
    for term, exempt in load_wordlist(wl_path):
        if term and term in text:
            if exempt and exempt in text:
                continue  # 豁免上下文命中（如"第一时间"豁免"第一"）
            violations.append(f"{prefix}: 命中词库「{name}」词条「{term}」")
    return violations


def precheck(text: str) -> dict:
    """写前检查：输入素材不含越界内容。返回 {ok, violations[]}。"""
    rules = load_rules()
    violations = []
    for term in rules.get("precheck_terms") or []:
        if term and term in text:
            violations.append(f"precheck: 输入含越界词「{term}」")
    return {"ok": not violations, "violations": violations}


def postcheck(text: str, min_chars: int | None = None) -> dict:
    """审后校验：成稿跑正则规则集 + wordlist 词库集。返回 {ok, violations[]}。

    min_chars 可临时覆盖字数下限（回归测试短样本用；缺省读规则集）。
    """
    rules = load_rules()
    violations = []
    for rule in rules.get("forbidden_patterns") or []:
        name = rule.get("name", "未命名规则")
        pat = rule.get("pattern", "")
        if not pat:
            continue
        flags = re.IGNORECASE if rule.get("ignore_case", True) else 0
        if re.search(pat, text, flags):
            violations.append(f"postcheck: 命中「{name}」({pat})")
    # M2/Δ4：L1' 补充词库（wordlist 规则类型）
    for rule in rules.get("wordlist_rules") or []:
        violations += _check_wordlist(text, rule)
    if min_chars is None:
        min_chars = int(rules.get("min_chars", 1200))
    # 去掉 markdown 标记和空白后计字数
    body = re.sub(r"[\s#>*`\[\]()\-_|]", "", text)
    if len(body) < min_chars:
        violations.append(f"postcheck: 字数下限不足（{len(body)} < {min_chars}）")
    return {"ok": not violations, "violations": violations}


def check(text: str, stage: str = "post") -> dict:
    """统一入口：stage='pre' 写前 / 'post' 审后。

    返回 {ok, violations[], warns[]}——G4/023：severity=warn 的词库命中分流到
    warns（不参与 ok 判定，013 R6 试运行兜底）；violations 只含 block 级。
    """
    res = precheck(text) if stage == "pre" else postcheck(text)
    res.setdefault("warns", [])
    res["warns"] = [v for v in res.get("violations", []) if v.startswith("postcheck-warn")]
    res["violations"] = [v for v in res.get("violations", [])
                         if not v.startswith("postcheck-warn")]
    return res
