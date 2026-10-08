.PHONY: install test test-mongo lint eval demo api console mcp doctor
install:   ## core + dev
	pip install -e ".[dev]"
test:
	pytest -q
test-mongo: ## production persistence path against a real MongoDB (set MERIDIAN_TEST_MONGODB_URI)
	pytest -q tests/integration/test_mongodb_production_path.py
lint:
	ruff check src tests scripts
eval:      ## evaluation harness + release gates
	python -m meridian.evaluation.benchmark --seed 7
	python -m meridian.evaluation.benchmark --seed 11
	python -m meridian.evaluation.benchmark --seed 23
demo:
	python scripts/run_demo.py
api:       ## API + Treasury Console at http://localhost:8000/
	uvicorn meridian.api.main:app --reload --port 8000
console: api
doctor:    ## production readiness check for the current environment
	meridian doctor
mcp:
	python -m meridian.mcp_server --http 8200
