"""跨平台 VLM 模型自动下载 — Windows/macOS/Linux 通用。

核心问题：setup.sh 是 bash 脚本，Windows 无法运行。
本模块用纯 Python 实现，三平台通用，可被两种方式调用：
  1. CLI: python -m archive_assistant.setup_model [options]
  2. 编程式: from archive_assistant.setup_model import ensure_model

设计要点：
  - 检测模型是否已就绪（config.json 存在）
  - 未就绪时自动下载（优先 huggingface-cli，回退 snapshot_download）
  - 检测 GPU/CPU 环境，给出 device 建议
  - pip 依赖缺失时提示安装命令（不自动 pip install，避免权限问题）
  - 全程日志输出，弱模型可读懂
"""
from __future__ import annotations

import os
import sys
import shutil
from typing import Optional


def _model_target_dir(cfg: Optional[dict] = None) -> str:
    """从 config.yaml 解析模型目标目录（展开 ~ 并规范化为绝对路径）。"""
    if cfg is None:
        from .utils import load_config
        cfg = load_config()
    vlm_cfg = cfg.get("vlm", {})
    local = vlm_cfg.get("local_dir", "~/models/Qwen3.5-4B")
    target = os.path.expanduser(local)
    # 相对路径 → 基于 skill 根目录解析
    if not os.path.isabs(target):
        here = os.path.dirname(os.path.abspath(__file__))
        skill_root = os.path.abspath(os.path.join(here, ".."))
        target = os.path.join(skill_root, target)
    return os.path.abspath(target)


def model_id(cfg: Optional[dict] = None) -> str:
    """返回 HF model_id（用于在线下载）。"""
    if cfg is None:
        from .utils import load_config
        cfg = load_config()
    return cfg.get("vlm", {}).get("model_id", "Qwen/Qwen3.5-4B")


def is_model_ready(target_dir: Optional[str] = None, cfg: Optional[dict] = None) -> bool:
    """检测模型是否已下载就绪（config.json 存在）。"""
    if target_dir is None:
        target_dir = _model_target_dir(cfg)
    return os.path.isdir(target_dir) and os.path.exists(
        os.path.join(target_dir, "config.json")
    )


def check_dependencies() -> dict:
    """检测 VLM 所需的 Python 依赖是否安装。返回 {dep: bool}。"""
    deps = {}
    for mod in ("torch", "transformers", "huggingface_hub", "accelerate"):
        try:
            __import__(mod)
            deps[mod] = True
        except ImportError:
            deps[mod] = False
    return deps


# Qwen3.5-4B（架构 qwen3_5）对 transformers 版本极其敏感，详见
# docs/QWEN_VISION_DIAGNOSIS.md。这里做明确的版本检查，避免弱模型
# 在版本不对时只看到"降级为规则模式"而不知原因。
RECOMMENDED_TRANSFORMERS = "5.6.0"


def check_transformers_version() -> dict:
    """检查 transformers 版本是否能正确驱动 qwen3_5 视觉推理。

    返回 {installed, ok, reason}：
      - ok=True   推荐版本（5.6.x 起、且非已知回归区间）
      - ok=False  太旧（不认识架构）或已知视觉回归版本
    """
    try:
        import transformers
        ver = transformers.__version__
    except ImportError:
        return {"installed": None, "ok": False,
                "reason": "transformers 未安装"}

    def _parse(v: str):
        nums = []
        for part in v.split("+")[0].split("."):
            try:
                nums.append(int(part))
            except ValueError:
                break
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:3])

    cur = _parse(ver)

    if cur < (5, 0, 0):
        return {"installed": ver, "ok": False,
                "reason": (f"transformers {ver} 太旧，无法识别 qwen3_5 架构"
                           f"（会加载失败并降级为规则模式）。"
                           f"请升级：pip install 'transformers=={RECOMMENDED_TRANSFORMERS}'")}
    if cur >= (5, 12, 0):
        return {"installed": ver, "ok": False,
                "reason": (f"transformers {ver} 存在 qwen3_5 视觉回归 bug"
                           f"（图片识别会输出幻觉文本）。"
                           f"请改用：pip install 'transformers=={RECOMMENDED_TRANSFORMERS}'")}
    return {"installed": ver, "ok": True,
            "reason": f"transformers {ver} 可用"}


def download_model(target_dir: str, repo_id: str) -> bool:
    """下载模型权重到 target_dir。优先 huggingface-cli，回退 snapshot_download。

    安全：不执行 shell 命令注入；huggingface-cli 通过 list 参数调用。
    """
    os.makedirs(target_dir, exist_ok=True)

    # 方式 1：huggingface-cli（如果安装了）
    cli = shutil.which("huggingface-cli")
    if cli:
        import subprocess
        print(f"[setup_model] 用 huggingface-cli 下载 {repo_id} → {target_dir}")
        try:
            subprocess.run(
                [cli, "download", repo_id,
                 "--local-dir", target_dir,
                 "--local-dir-use-symlinks", "False"],
                check=True,
            )
            if is_model_ready(target_dir):
                return True
        except Exception as e:
            print(f"[setup_model] huggingface-cli 失败: {e}，尝试 Python API...")

    # 方式 2：Python snapshot_download
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[setup_model] huggingface_hub 未安装！请运行：pip install huggingface-hub")
        return False

    print(f"[setup_model] 用 snapshot_download 下载 {repo_id} → {target_dir}")
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_dir,
            local_dir_use_symlinks=False,
        )
        return is_model_ready(target_dir)
    except Exception as e:
        print(f"[setup_model] 下载失败: {e}")
        return False


def ensure_model(auto_download: bool = True, cfg: Optional[dict] = None) -> dict:
    """确保模型就绪。返回状态 dict。

    如果模型已就绪 → 直接返回 ready=True。
    如果未就绪且 auto_download=True → 自动下载。
    如果未就绪且 auto_download=False → 返回 ready=False + 安装指引。
    """
    if cfg is None:
        from .utils import load_config
        cfg = load_config()

    target = _model_target_dir(cfg)
    repo = model_id(cfg)

    # 1. 检查依赖
    deps = check_dependencies()
    missing = [k for k, v in deps.items() if not v]

    # 1b. 检查 transformers 版本（qwen3_5 对版本敏感）
    tf_ver = check_transformers_version()

    # 2. 检查模型
    ready = is_model_ready(target, cfg)

    if ready and not missing:
        return {
            "ready": True,
            "target_dir": target,
            "model_id": repo,
            "deps_ok": True,
            "missing_deps": [],
            "transformers_ok": tf_ver["ok"],
            "transformers_version": tf_ver["installed"],
            "message": (f"模型已就绪: {target}" if tf_ver["ok"]
                        else f"模型已就绪: {target}\n⚠️ {tf_ver['reason']}"),
        }

    if not ready and auto_download and not missing:
        print(f"[setup_model] 模型未就绪，开始下载...")
        ok = download_model(target, repo)
        return {
            "ready": ok,
            "target_dir": target,
            "model_id": repo,
            "deps_ok": True,
            "missing_deps": [],
            "transformers_ok": tf_ver["ok"],
            "transformers_version": tf_ver["installed"],
            "message": ("模型下载完成" if ok else "模型下载失败"),
        }

    # 依赖缺失或未就绪且不自动下载
    return {
        "ready": False,
        "target_dir": target,
        "model_id": repo,
        "deps_ok": len(missing) == 0,
        "missing_deps": missing,
        "transformers_ok": tf_ver["ok"],
        "transformers_version": tf_ver["installed"],
        "message": _format_missing_message(missing, target, repo),
    }


def _format_missing_message(missing: list, target: str, repo: str) -> str:
    if not missing:
        return f"模型未就绪: {target}（未下载）"
    deps_str = " ".join(missing)
    return (
        f"缺少依赖: {', '.join(missing)}\n"
        f"请运行:\n"
        f"  pip install {deps_str}\n"
        f"然后运行:\n"
        f"  python -m archive_assistant.setup_model\n"
        f"下载模型 {repo} → {target}"
    )


def detect_device() -> str:
    """检测最佳设备：cuda / mps / cpu。"""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


# ---------- CLI 入口 ----------

def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        "setup_model",
        description="跨平台自动下载 VLM 模型权重（Windows/macOS/Linux 通用）",
    )
    p.add_argument("--check", action="store_true", help="仅检查状态，不下载")
    p.add_argument("--no-download", action="store_true", help="不自动下载，只输出指引")
    p.add_argument("--install-deps", action="store_true",
                   help="自动 pip install 缺失依赖（需要网络）")
    args = p.parse_args(argv)

    from .utils import load_config
    cfg = load_config()
    target = _model_target_dir(cfg)
    repo = model_id(cfg)

    print(f"模型 ID:  {repo}")
    print(f"目标目录: {target}")
    print(f"设备:     {detect_device()}")

    deps = check_dependencies()
    print(f"\n依赖检测:")
    for k, v in deps.items():
        print(f"  {'✅' if v else '❌'} {k}")

    missing = [k for k, v in deps.items() if not v]

    # transformers 版本检查（qwen3_5 视觉对版本敏感）
    if not missing or deps.get("transformers"):
        tf = check_transformers_version()
        print(f"\ntransformers 版本: {'✅' if tf['ok'] else '⚠️'} {tf['reason']}")
        if not tf["ok"]:
            print(f"  推荐: pip install 'transformers=={RECOMMENDED_TRANSFORMERS}'")

    # 自动安装依赖
    if missing and args.install_deps:
        print(f"\n安装缺失依赖: {', '.join(missing)}")
        import subprocess
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing,
            check=False,
        )
        deps = check_dependencies()
        missing = [k for k, v in deps.items() if not v]

    ready = is_model_ready(target, cfg)
    print(f"\n模型就绪: {'✅ 是' if ready else '❌ 否'}")

    if args.check:
        return 0 if ready and not missing else 1

    if ready and not missing:
        print("\n✅ 一切就绪！可以运行:")
        print("  python -m archive_assistant.cli.main auto \"整理桌面\" --root ~/Desktop")
        return 0

    if missing:
        print(f"\n❌ 缺少依赖: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        print(f"或:     python -m archive_assistant.setup_model --install-deps")
        return 1

    if args.no_download:
        print(f"\n模型未下载。请运行: python -m archive_assistant.setup_model")
        return 1

    # 自动下载
    print(f"\n开始下载模型 {repo} → {target} ...")
    ok = download_model(target, repo)
    if ok:
        print(f"\n✅ 下载完成！可以运行:")
        print("  python -m archive_assistant.cli.main auto \"整理桌面\" --root ~/Desktop")
        return 0
    else:
        print(f"\n❌ 下载失败。请检查网络连接，或手动运行:")
        print(f"  huggingface-cli download {repo} --local-dir \"{target}\"")
        return 1


if __name__ == "__main__":
    sys.exit(main())
