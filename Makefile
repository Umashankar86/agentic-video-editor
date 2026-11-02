.PHONY: run-worker run-api test fmt docker-up clean-runtime

run-worker:
	python -m uvicorn worker.app.main:app --reload --port 8000

run-api:
	go run ./cmd/orchestrator

test:
	go test ./...
	python -m pytest worker/tests -q

fmt:
	gofmt -w cmd internal
	python -m compileall -q worker

docker-up:
	docker compose up --build

clean-runtime:
	@echo "Delete files inside uploads/, work/, and outputs/ manually when no jobs are running."

