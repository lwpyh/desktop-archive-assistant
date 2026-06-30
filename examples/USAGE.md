# Examples

## 1) 桌面快速试跑（不下模型，使用规则版 VLM）

```bash
cd /Youtu_VITA/jiannhu/qclaw_youtu_skills/content-archive-assistant
python -m archive_assistant.cli.main \
    desktop ~/Desktop \
    --dry-run --out /tmp/desk_plan.json \
    --no-vlm
cat /tmp/desk_plan.json | head -80

python -m archive_assistant.cli.main \
    desktop --apply --plan /tmp/desk_plan.json --no-vlm
```

## 2) 真实 Qwen3.5-4B 跑

```bash
bash scripts/setup.sh                        # 下模型到 ./models/Qwen3.5-4B
python -m archive_assistant.cli.main \
    photo ~/Pictures \
    --categories "旅行,家庭,工作截图,证件,美食" \
    --dry-run --out /tmp/photo_plan.json
```

## 3) 后悔药

```bash
python -m archive_assistant.cli.main rollback --last
```
