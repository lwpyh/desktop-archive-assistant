# Qwen3.5-4B 视觉推理问题 —— 诊断报告（已解决 ✅）

日期：2026-06-22
环境：Linux + ROCm 7.0 + PyTorch 2.8.0+rocm + Python 3.12

## 结论

**根因：transformers 5.12.1 对 `qwen3_5` 的视觉推理有回归 bug —— 不论输入什么图片
（甚至纯红/蓝/绿合成图）都输出相同的幻觉文本（"metal rods / stage lighting"），
视觉特征没有真正参与解码。**

**解决方案：transformers 降到 5.6.0 + dtype=float16，视觉能力完全恢复。**

验证（5.6.0 + fp16）：
- 纯红 / 蓝 / 绿图 → 正确答 red / blue / green ✅
- 鬼脸父子图 → "a man and two children making exaggerated facial expressions" ✅
- 玻璃幕墙建筑 → "a person walking in front of a large modern building" ✅

## 正确版本组合（已写入 requirements.txt）

```
transformers==5.6.0          # 不能用 5.12.x（视觉回归）；5.0 与 hub 1.x 冲突
huggingface-hub>=1.0
tokenizers>=0.22
# config.yaml: vlm.dtype = float16   （ROCm 上 bfloat16 会 mode-collapse）
```

## 版本排查矩阵

| 版本 | 加载 qwen3_5 | 视觉 | 备注 |
|---|---|---|---|
| 4.53.3 | ❌ | - | 太旧，不认识架构 |
| 4.57.1 | ❌ | - | 正式版未合入（模型是 4.57.0.dev0）|
| 5.0.0 | ✅ | 未测 | 与系统 hub 1.x 依赖冲突，放弃 |
| **5.6.0** | ✅ | **✅ 正常** | **最终采用** |
| 5.12.1 | ✅ | ❌ collapse | 视觉回归 bug |

## 已排除的非根因（5.12.1 时期逐一验证）

- 图片输入路径：✅ 正常（process_vision_info / PIL 直传都对）
- image token 对齐：✅ `<|image_pad|>`=248056，192 token 与 grid 24×32/4 匹配
- vision tower 权重：✅ 297 个 visual.* 参数正常加载（非随机）
- KV-cache 清理 / 采样策略 / dtype 切换 / prompt 写法 / flash-linear-attention：❌ 均无改善

→ 全部排除后，定位到 transformers 版本本身。

## 验证脚本
- `scripts/caption_then_cluster.py` — caption 驱动聚类
- 诊断日志：/tmp/t560.log（5.6.0 成功）、/tmp/diag*.log（5.12.1 失败）
