---
description: VRAM の使用状況を確認する
---

以下を実行し、VRAM 8GB の制約に対して余裕があるか評価してください。

```bash
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv
docker compose exec -T ollama ollama ps
```

評価の観点:
- 使用量が 7GB を超えていたら危険。原因のモデルを特定する
- `ollama ps` に複数モデルがロードされていたら設定ミス（`OLLAMA_MAX_LOADED_MODELS=1` のはず）
- Embedding モデルが GPU 側にロードされていないか（CPU 側の ollama-embed にあるべき）

問題があれば、`docs/ARCHITECTURE.md` の VRAM 配分表と照らして原因と対処を報告してください。
