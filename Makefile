.PHONY: run compose-up compose-down logs health register

run:
	uvicorn src.main:app --reload --host 127.0.0.1 --port 8778

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down

logs:
	docker compose logs -f

health:
	curl -fsS http://127.0.0.1:8778/health

register:
	python register.py --pms-token $$PLEX_TOKEN
