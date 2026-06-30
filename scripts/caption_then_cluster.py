"""Caption-driven 分类实验脚本：
1) 从指定目录随机抽 N 张图
2) 对每张图调用 Qwen3.5-4B caption 出一段中文短语
3) 用文本 embedding 对 caption 做聚类
4) 用 Qwen3.5 给每个簇起一个统一的相册名
5) 输出 JSON 报告 + 马赛克可视化
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archive_assistant.utils import load_config, logger, safe_folder_name
from archive_assistant.vlm import VLMReasoner
from archive_assistant.extractors.embedder import embed_texts


def collect(root: str, n: int, seed: int = 42) -> List[str]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    files = [os.path.join(root, f) for f in sorted(os.listdir(root))
             if os.path.splitext(f)[1].lower() in exts]
    random.Random(seed).shuffle(files)
    return files[:n]


def caption_all(vlm: VLMReasoner, paths: List[str]) -> List[Dict]:
    """对每张图调用 caption；记录耗时与失败。"""
    rows = []
    for i, fp in enumerate(paths):
        t = time.time()
        try:
            cap = vlm.caption(fp) or ""
        except Exception as e:                # noqa: BLE001
            logger.warning("caption failed on %s: %s", fp, e)
            cap = ""
        dt = time.time() - t
        # 太短 / 看起来像复读 prompt 的，直接清空
        if len(cap) < 2 or "用户" in cap or "短语" in cap or "示例" in cap:
            cap = ""
        rows.append({"path": fp, "caption": cap, "ms": int(dt * 1000)})
        if (i + 1) % 5 == 0 or i == 0:
            print(f"  [{i + 1}/{len(paths)}] {os.path.basename(fp):20s}  '{cap}'  ({dt:.1f}s)")
    return rows


def cluster_by_caption(rows: List[Dict], text_model_id: str,
                       sim_thresh: float = 0.55) -> List[List[int]]:
    """用文本 embedding 做贪心聚类。空 caption 自成一簇 'unknown'。"""
    captions = [r["caption"] for r in rows]
    # 全空的填占位
    embed_input = [c if c else "未识别图片" for c in captions]
    vecs = np.array(embed_texts(embed_input, model_id=text_model_id), dtype=np.float32)

    centers: List[np.ndarray] = []
    members: List[List[int]] = []
    for i, v in enumerate(vecs):
        if not captions[i]:
            # 空 caption 的整体合并到一个 "unknown" 簇
            best = -1
            for j, c in enumerate(centers):
                if not rows[members[j][0]]["caption"]:
                    best = j; break
            if best < 0:
                centers.append(v); members.append([i])
            else:
                members[best].append(i)
            continue
        sims = [float(v @ c) for c in centers] if centers else []
        best, best_s = (-1, -1.0)
        for j, s in enumerate(sims):
            # 跳过 unknown 簇
            if not rows[members[j][0]]["caption"]:
                continue
            if s > best_s:
                best, best_s = j, s
        if best >= 0 and best_s >= sim_thresh:
            members[best].append(i)
        else:
            centers.append(v); members.append([i])
    return members


def name_clusters(vlm: VLMReasoner, rows: List[Dict],
                  groups: List[List[int]]) -> List[Dict]:
    """每个 cluster 用 Qwen3.5 给出统一的相册名（看 1-3 张代表图）。"""
    out = []
    for gi, idx_list in enumerate(groups):
        captions = [rows[i]["caption"] for i in idx_list if rows[i]["caption"]]
        rep_paths = [rows[i]["path"] for i in idx_list[:3]]
        if not captions:
            label = "未识别"
        else:
            # 用最常见 caption 作为兜底名
            from collections import Counter
            most_common = Counter(captions).most_common(1)[0][0]
            label = most_common
            # 如果簇内 caption 高度一致，直接用；否则让 Qwen 看图融合
            if len(set(captions)) > 1 and rep_paths:
                merged = vlm.caption(rep_paths[0]) or label
                if 2 <= len(merged) <= 12:
                    label = merged
        out.append({
            "cluster_id": f"c{gi:02d}",
            "label": safe_folder_name(label),
            "size": len(idx_list),
            "indices": idx_list,
            "captions_sample": captions[:5],
        })
    return out


def make_mosaic(rows: List[Dict], clusters: List[Dict], out_path: str,
                tile: int = 140, max_per_row: int = 8) -> None:
    rows_data = [(c, [rows[i] for i in c["indices"][:max_per_row]]) for c in clusters]
    H = len(rows_data) * tile + 30
    W = (max_per_row + 1) * tile
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
    for r, (cluster, items) in enumerate(rows_data):
        y = r * tile + 10
        draw.text((4, y + tile // 2 - 8),
                  f"{cluster['label'][:18]}\n(n={cluster['size']})",
                  fill="black", font=font)
        for j, item in enumerate(items):
            try:
                with Image.open(item["path"]) as im:
                    im = im.convert("RGB")
                    im.thumbnail((tile, tile))
                    x = (j + 1) * tile + (tile - im.size[0]) // 2
                    yo = y + (tile - im.size[1]) // 2
                    canvas.paste(im, (x, yo))
            except Exception:                  # noqa: BLE001
                pass
    canvas.save(out_path, quality=85)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="image directory")
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sim-thresh", type=float, default=0.55,
                    help="caption embedding similarity threshold (cosine)")
    ap.add_argument("--out-json", default="/tmp/caption_clusters.json")
    ap.add_argument("--out-mosaic", default="/tmp/caption_clusters.jpg")
    args = ap.parse_args()

    cfg = load_config()
    print("[1/4] init Qwen3.5-4B ...")
    vlm = VLMReasoner(cfg)
    if vlm.is_fallback:
        print("ERROR: VLM fell back to rule-based; cannot proceed.")
        sys.exit(1)

    print(f"[2/4] sampling {args.n} images from {args.root}")
    paths = collect(args.root, args.n, seed=args.seed)
    print(f"      sampled {len(paths)} files")

    print(f"[3/4] captioning each image with Qwen3.5-4B ...")
    t0 = time.time()
    rows = caption_all(vlm, paths)
    print(f"      done in {time.time() - t0:.1f}s "
          f"(avg {(time.time() - t0) / len(paths):.1f}s/img)")

    print(f"[4/4] clustering by caption + naming ...")
    groups = cluster_by_caption(rows, cfg["embedding"]["text_model"],
                                sim_thresh=args.sim_thresh)
    clusters = name_clusters(vlm, rows, groups)
    clusters.sort(key=lambda c: -c["size"])

    report = {
        "root": args.root,
        "n_sampled": len(paths),
        "n_clusters": len(clusters),
        "rows": rows,
        "clusters": clusters,
    }
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    make_mosaic(rows, clusters, args.out_mosaic)

    print()
    print("===== Result =====")
    for c in clusters:
        print(f"  📁 {c['label']:20s} n={c['size']:3d}   "
              f"sample captions: {c['captions_sample'][:3]}")
    print()
    print(f"JSON  -> {args.out_json}")
    print(f"Mosaic-> {args.out_mosaic}")


if __name__ == "__main__":
    main()
