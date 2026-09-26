.PHONY: web worker test

web:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

worker:
	python -m app.worker

test:
	pytest
