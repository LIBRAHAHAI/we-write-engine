# -*- coding: utf-8 -*-
"""5 个演练实例开 allow_test_dispatch（hermes t_2c740cd9 守卫的放行路径①：
演练实例在 state 显式置 true——与 novel-pilot 同口径，非绕守卫）。"""
import json
from pathlib import Path

INST = Path("./engine/instances")

for name in ["fa-pilot", "hn-pilot", "bm-pilot", "nl-pilot", "sc-pilot"]:
    p = INST / f"{name}.json"
    st = json.loads(p.read_text(encoding="utf-8"))
    st["allow_test_dispatch"] = True
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{name}: allow_test_dispatch=True (template={st.get('template')}, mode={st.get('mode')})")
