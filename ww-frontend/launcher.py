"""
写作引擎看板 — 桌面启动器（仿 flow-engine-frontend launcher 模式）
双击桌面快捷方式 → 启动 ww_panel(8660) + 后端(8670) + 前端(5175) → 打开 Edge --app 桌面窗口

用法：
  python launcher.py              # 前台启动
  python launcher.py --debug      # 保留控制台窗口（调试用）
  python launcher.py --no-window  # 只起服务不开窗口

三个服务（端口检测防重复，已在跑的直接复用）：
  1. ww_panel    8660 — 引擎自带仪表盘（保持独立运行，前端仪表盘页 iframe 嵌入）
  2. 后端 API    8670 — FastAPI 包装层（backend/api.py）
  3. 前端        5175 — vite preview 加载 frontend/dist 构建产物

依赖：FastAPI + uvicorn（后端）、vite（前端构建产物）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ── 路径常量 ──────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

# 被包装的引擎目录（只读，不修改其中任何文件）
ENGINE_DIR = Path(r"./engine")
WW_PANEL_PY = ENGINE_DIR / "ww_panel.py"

PANEL_PORT = 8660    # ww_panel 独立仪表盘
API_PORT = 8670      # ww-frontend 后端 API
FRONTEND_PORT = 5175 # ww-frontend 前端

# ── 工具函数 ──────────────────────────────────────────────


def is_port_open(port: int) -> bool:
    """端口可达性探测：API 端口打 /api/health，其余打根路径。"""
    try:
        url = (f"http://localhost:{port}/api/health" if port == API_PORT
               else f"http://localhost:{port}/")
        r = urllib.request.urlopen(url, timeout=2)
        return r.status == 200
    except Exception:
        return False


def wait_for_port(port: int, timeout: int = 15) -> bool:
    for _ in range(timeout):
        if is_port_open(port):
            return True
        time.sleep(1)
    return False


def spawn(cmd: list[str], cwd: Path, debug: bool) -> None:
    """跨版本兼容的子进程启动（Windows 编组分离，非调试模式静默输出）。"""
    subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL if not debug else subprocess.PIPE,
        stderr=subprocess.DEVNULL if not debug else subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )


def find_edge() -> str | None:
    """查找 Edge 可执行文件路径"""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


# ── 主流程 ────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="写作引擎看板桌面启动器")
    ap.add_argument("--debug", action="store_true", help="保留控制台窗口")
    ap.add_argument("--no-window", action="store_true", help="只起服务不开窗口")
    args = ap.parse_args()

    print("⚙️ 写作引擎看板启动中...")

    # 1. 启动 ww_panel（引擎自带仪表盘，8660，保持独立跑）
    if is_port_open(PANEL_PORT):
        print(f"  ✅ ww_panel 已在运行 (端口 {PANEL_PORT})")
    elif not WW_PANEL_PY.exists():
        print(f"  ⚠️ 未找到 ww_panel.py: {WW_PANEL_PY}（跳过，仪表盘页将显示离线提示）")
    else:
        print(f"  🚀 启动 ww_panel (端口 {PANEL_PORT})...")
        spawn([sys.executable, str(WW_PANEL_PY)], ENGINE_DIR, args.debug)
        if not wait_for_port(PANEL_PORT, timeout=15):
            print(f"  ⚠️ ww_panel 启动失败（端口 {PANEL_PORT} 未就绪），继续起其余服务")
        else:
            print(f"  ✅ ww_panel 就绪 (端口 {PANEL_PORT})")

    # 2. 启动 FastAPI 后端
    if is_port_open(API_PORT):
        print(f"  ✅ 后端 API 已在运行 (端口 {API_PORT})")
    else:
        print(f"  🚀 启动后端 API (端口 {API_PORT})...")
        spawn(
            [sys.executable, "-c",
             "import uvicorn; from api import app; uvicorn.run(app, host='0.0.0.0', port=%d, log_level='warning')" % API_PORT],
            BACKEND_DIR,
            args.debug,
        )
        if not wait_for_port(API_PORT, timeout=15):
            print(f"  ❌ 后端启动失败（端口 {API_PORT} 未就绪）")
            return 1
        print(f"  ✅ 后端 API 就绪 (端口 {API_PORT})")

    # 3. 启动前端（vite preview 加载 dist 产物）
    if not FRONTEND_DIST.exists():
        print(f"  ❌ 前端构建产物不存在: {FRONTEND_DIST}")
        print(f"     请先运行: cd {FRONTEND_DIR} && npm install && npm run build")
        return 1

    if is_port_open(FRONTEND_PORT):
        print(f"  ✅ 前端已在运行 (端口 {FRONTEND_PORT})")
    else:
        print(f"  🚀 启动前端 (端口 {FRONTEND_PORT})...")
        # Windows 下 npx 需要完整路径或 .cmd 后缀
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        spawn(
            [npx, "vite", "preview", "--port", str(FRONTEND_PORT), "--host"],
            FRONTEND_DIR,
            args.debug,
        )
        if not wait_for_port(FRONTEND_PORT, timeout=15):
            print(f"  ❌ 前端启动失败（端口 {FRONTEND_PORT} 未就绪）")
            return 1
        print(f"  ✅ 前端就绪 (端口 {FRONTEND_PORT})")

    # 4. 打开 Edge --app 桌面窗口
    if args.no_window:
        print("  --no-window 模式，跳过窗口")
        print("\n✅ 服务已就绪：")
        print(f"   前端面板: http://localhost:{FRONTEND_PORT}")
        print(f"   后端 API: http://localhost:{API_PORT}/api")
        print(f"   API 文档: http://localhost:{API_PORT}/docs")
        print(f"   ww_panel: http://localhost:{PANEL_PORT}/")
        print("\n   按 Ctrl+C 停止所有服务")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    edge = find_edge()
    if not edge:
        print("  ⚠️ 未找到 Edge，用默认浏览器打开")
        import webbrowser
        webbrowser.open(f"http://localhost:{FRONTEND_PORT}")
    else:
        print(f"  🖥️ 打开桌面窗口...")
        subprocess.Popen([
            edge,
            "--app=" + f"http://localhost:{FRONTEND_PORT}",
            "--app-size=1280,800",
            "--app-name=写作引擎看板",
        ])

    print("\n✅ 写作引擎看板已启动！")
    print(f"   窗口已打开，关闭窗口后服务继续运行")
    print(f"   前端: http://localhost:{FRONTEND_PORT}")
    print(f"   API:  http://localhost:{API_PORT}/api")
    print(f"   面板: http://localhost:{PANEL_PORT}/")

    # 保持脚本运行（窗口关闭后服务仍跑）
    if not args.debug:
        # 非调试模式：脚本退出，子进程独立运行
        return 0
    else:
        print("\n   调试模式：按 Ctrl+C 停止所有服务")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
