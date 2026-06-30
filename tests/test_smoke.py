"""端到端冒烟测试：完全离线、不依赖 VLM、不依赖 transformers/sentence-transformers。

覆盖当前真实 CLI 流程：
  1) organize <root> --out plan.json   生成计划（不动盘）
  2) apply --plan plan.json            执行计划（mkdir/move）
  3) rollback --last                   按日志回撤
另含 archive-by-date --dry-run 的不动盘冒烟。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# 让 tests/ 能 import 到包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive_assistant.cli.main import main as cli_main  # noqa: E402


def _touch(path: str, content: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_tiny_png(path: str) -> None:
    # 写一个 1x1 PNG（最小合法字节流）
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(png_b64))


def _write_tmp_config(tmp: str, log_dir: str) -> str:
    """写一个临时 config.yaml，把 executor.log_dir 指到临时目录，便于 apply/rollback 闭环。"""
    from archive_assistant.utils import load_config
    import yaml
    cfg = load_config()
    cfg["executor"]["log_dir"] = log_dir
    cfg_path = os.path.join(tmp, "config.yaml")
    with open(cfg_path, "w") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    return cfg_path


class TestOrganizeFlow(unittest.TestCase):
    """organize -> apply -> rollback 主流程。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="archive_smoke_")
        self.root = os.path.join(self.tmp, "Desktop")
        os.makedirs(self.root)
        # 制造若干文件：文本 + 一个视频 + 一个音频（按规则会路由到 Videos/Audios）
        _touch(os.path.join(self.root, "cvpr_rebuttal.txt"), "CVPR rebuttal draft method experiments")
        _touch(os.path.join(self.root, "reviewer_notes.txt"), "reviewer comments rebuttal response")
        _touch(os.path.join(self.root, "invoice_2026.txt"), "invoice payment amount due 2026")
        _touch(os.path.join(self.root, "movie.mp4"), "")   # -> Videos
        _touch(os.path.join(self.root, "song.mp3"), "")    # -> Audios

        self.plan = os.path.join(self.tmp, "plan.json")
        self.log_dir = os.path.join(self.tmp, "log")
        self.cfg = _write_tmp_config(self.tmp, self.log_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_organize_then_apply_then_rollback(self):
        movie = os.path.join(self.root, "movie.mp4")

        # 1) organize 生成计划（不动盘）
        rc = cli_main([
            "--config", self.cfg, "--no-vlm",
            "organize", self.root, "--out", self.plan,
        ])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.plan))
        with open(self.plan) as f:
            plan = json.load(f)
        self.assertEqual(plan["mode"], "desktop")
        self.assertGreater(len(plan["actions"]), 0)
        # 生成计划阶段不应移动任何文件
        self.assertTrue(os.path.exists(movie))

        # 2) apply 执行计划
        rc = cli_main([
            "--config", self.cfg, "--no-vlm",
            "apply", "--plan", self.plan,
        ])
        self.assertEqual(rc, 0)
        # movie.mp4 应被移出顶层，进入 Videos/ 子目录
        self.assertFalse(os.path.exists(movie))
        videos_dir = os.path.join(self.root, "Videos")
        self.assertTrue(os.path.isdir(videos_dir))
        self.assertTrue(os.path.exists(os.path.join(videos_dir, "movie.mp4")))

        # 3) rollback 回撤（从临时 log_dir 取最近一次日志）
        rc = cli_main([
            "--config", self.cfg, "--no-vlm",
            "rollback", "--last",
        ])
        self.assertEqual(rc, 0)
        # 回撤后 movie.mp4 应回到原位
        self.assertTrue(os.path.exists(movie))


class TestArchiveByDateDryRun(unittest.TestCase):
    """archive-by-date --dry-run 冒烟：仅预览，不动盘。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="archive_photo_smoke_")
        self.album = os.path.join(self.tmp, "Pictures")
        os.makedirs(self.album)
        # 三张 PNG，错开 mtime（无 EXIF 时按修改时间归档）
        for i in range(3):
            _make_tiny_png(os.path.join(self.album, f"img_{i}.png"))
        now = time.time()
        os.utime(os.path.join(self.album, "img_0.png"), (now, now))
        os.utime(os.path.join(self.album, "img_1.png"), (now, now))
        old = now - 7 * 24 * 3600
        os.utime(os.path.join(self.album, "img_2.png"), (old, old))

        self.log_dir = os.path.join(self.tmp, "log")
        self.cfg = _write_tmp_config(self.tmp, self.log_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_by_date_dry_run_no_move(self):
        rc = cli_main([
            "--config", self.cfg, "--no-vlm",
            "archive-by-date", self.album, "--level", "day", "--dry-run",
        ])
        self.assertEqual(rc, 0)
        # dry-run 不应移动任何文件，三张图仍在顶层
        for i in range(3):
            self.assertTrue(os.path.exists(os.path.join(self.album, f"img_{i}.png")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
