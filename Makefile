.PHONY: setup up down logs ps pull-models migrate revision seed fmt test test-report smoke gpu clean

DC := docker compose

setup:
	bash scripts/setup-host.sh

up:
	@test -f .env || (echo "ERROR: .env がありません。cp .env.example .env してください" && exit 1)
	$(DC) up -d --build

down:
	$(DC) down

logs:
	$(DC) logs -f --tail=100

ps:
	$(DC) ps

pull-models:
	bash scripts/pull-models.sh

migrate:
	$(DC) exec gateway python -m alembic upgrade head

revision:
	@test -n "$(m)" || (echo 'ERROR: make revision m="説明" の形で実行してください' && exit 1)
	$(DC) exec gateway python -m alembic revision --autogenerate -m "$(m)"

seed:
	$(DC) exec gateway python -m scripts.seed

fmt:
	$(DC) run --rm --no-deps gateway ruff format app scripts
	$(DC) run --rm --no-deps gateway ruff check --fix app scripts

test:
	$(DC) run --rm --no-deps gateway pytest -q

# reports/test-report.md と reports/verification.json を生成する。
# .claude/hooks/guard-gh.ps1 がこの記録を見て gh pr create を許可するかを判定する。
test-report:
	python3 scripts/gen_report.py

smoke:
	bash scripts/smoke.sh

gpu:
	nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu \
		--format=csv,noheader

clean:
	$(DC) down -v
	@echo "モデルのボリュームも削除しました。再ダウンロードが必要です。"
