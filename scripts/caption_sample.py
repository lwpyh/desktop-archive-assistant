"""随机抽取 N 张图，用 Qwen3.5-4B 逐张 caption。

用法:
  python scripts/caption_sample.py <image_dir> -n 5 [--seed 42] [--out /tmp/caps.json]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive_assistant.utils import load_config, logger
from archive_assistant.vlm import VLMReasoner


def collect(root: str, n: int, seed: int) -> List[str]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    files = [os.path.join(root, f) for f in sorted(os.listdir(root))
             if os.path.splitext(f)[1].lower() in exts]
    random.Random(seed).shuffle(files)
    return files[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="image directory")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="/tmp/caption_sample.json")
    args = ap.parse_args()

    cfg = load_config()
    print("[1/3] init Qwen3.5-4B ...")
    vlm = VLMReasoner(cfg)
    if vlm.is_fallback:
        print("ERROR: VLM fell back to rule-based; model not loaded.")
        sys.exit(1)

    print(f"[2/3] sampling {args.n} images from {args.root} (seed={args.seed})")
    paths = collect(args.root, args.n, args.seed)
    for p in paths:
        print(f"      - {os.path.basename(p)}")

    print(f"[3/3] captioning with Qwen3.5-4B ...\n")
    rows = []
    t0 = time.time()
    for i, fp in enumerate(paths):
        t = time.time()
        try:
            cap = vlm.caption(fp) or ""
        except Exception as e:                 # noqa: BLE001
            logger.warning("caption failed on %s: %s", fp, e)
            cap = ""
        dt = time.time() - t
        rows.append({"path": fp, "file": os.path.basename(fp),
                     "caption": cap, "ms": int(dt * 1000)})
        print(f"  [{i + 1}/{len(paths)}] {os.path.basename(fp):18s}  ->  '{cap}'  ({dt:.1f}s)")

    report = {"root": args.root, "seed": args.seed,
              "n": len(paths), "rows": rows}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n===== Captions =====")
    for r in rows:
        print(f"  {r['file']:18s}  {r['caption']!r}")
    print(f"\ntotal {time.time() - t0:.1f}s   JSON -> {args.out}")


if __name__ == "__main__":
    main()
