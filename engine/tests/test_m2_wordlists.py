"""test_m2_wordlists.py — L1' 四词库 S3 回归（013 Δ4 / L-1 判据）。

验收硬标准：每词库 ≥10 违规样本 100% 拦截、干净稿零误报、豁免语法生效。
"""
import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import discipline  # noqa: E402

# 每词库 10+ 违规样本（S3 回归集，直接从词库词条构造短句）
SAMPLE_VIOLATIONS = {
    "ad_law.txt": ["这是全网最好的产品", "效果百分百有效", "销量第一的品牌",
                   "包治百病的神药", "绝无仅有的机会", "极品装备",
                   "史上最强的方案", "根治顽疾", "零风险承诺", "行业第一"],
    "medical.txt": ["三天见效的神方", "祖传秘方治大病", "无任何副作用",
                    "治愈率高达九成", "特效药上市", "包治百病", "药到病除",
                    "无需就医", "停药即可", "在线开药"],
    "finance.txt": ["稳赚不赔的项目", "保本保收益", "内幕消息荐股",
                    "老师带单稳赢", "躺着赚钱的副业", "财富密码曝光",
                    "月入过万不是梦", "零风险投资", "刚性兑付承诺", "拉群荐股"],
    "fraud.txt": ["刷单兼职日结", "点赞返现活动", "转账到指定账户",
                  "杀猪盘受害者", "缴纳解冻费", "下载指定APP操作",
                  "不要告诉家人", "垫付本金做任务", "扫码领红包", "卡单需再充"],
}

CLEAN_SAMPLES = [
    "这是我们实测下来比较顺手的方法，但效果因人而异。",
    "他第一时间赶到现场，记录了第一手资料。",
    "魏晋名士爱喝酒，表面是风流，深看是逃生。",  # 真实 CLEAN 样本句
    "医生建议定期体检，患病应到正规医院就诊。",
    "理财有风险，投资需谨慎，历史收益不代表未来表现。",
    "接到陌生转账要求应报警核实，全民反诈意识在提升。",
]


class TestL1WordlistsS3(unittest.TestCase):
    def test_each_wordlist_blocks_all_violation_samples(self):
        for wl, samples in SAMPLE_VIOLATIONS.items():
            self.assertGreaterEqual(len(samples), 10, f"{wl} 样本须 ≥10")
            path = discipline.WORDLIST_DIR / wl
            loaded = discipline.load_wordlist(path)
            self.assertGreaterEqual(len(loaded), 25, f"{wl} 词库词条须 ≥25")
            for s in samples:
                res = discipline._check_wordlist(s, {"name": wl, "wordlist": wl})
                self.assertTrue(res, f"{wl} 样本未拦截: {s}")

    def test_clean_samples_zero_false_positive(self):
        for s in CLEAN_SAMPLES:
            res = discipline.postcheck(s, min_chars=0)
            wl_hits = [v for v in res["violations"] if "词库" in v]
            self.assertEqual(wl_hits, [], f"干净稿误报: {s!r} → {wl_hits}")

    def test_exempt_context_syntax(self):
        """`第一|第一时间` 豁免：含'第一时间'的句子不得因'第一'被拦（若'第一'入词库）。"""
        wl = discipline.WORDLIST_DIR / "ad_law.txt"
        tmp = wl.with_suffix(".tmp_test.txt")
        tmp.write_text("第一|第一时间\n", encoding="utf-8")
        try:
            # 含豁免上下文 → 放行
            r1 = discipline._check_wordlist("他第一时间反应过来",
                                            {"name": "t", "wordlist": tmp.name})
            self.assertEqual(r1, [])
            # 无豁免上下文 → 拦截
            r2 = discipline._check_wordlist("全网第一的销量",
                                            {"name": "t", "wordlist": tmp.name})
            self.assertTrue(r2)
        finally:
            tmp.unlink(missing_ok=True)

    def test_wordlist_integrated_in_postcheck(self):
        res = discipline.postcheck("稳赚不赔的项目快点来", min_chars=0)
        self.assertFalse(res["ok"])
        self.assertTrue(any("金融违规" in v or "finance" in v
                            for v in res["violations"]), res["violations"])


if __name__ == "__main__":
    unittest.main()
