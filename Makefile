.PHONY: web worker test

web:
	uvicorn app.main:app --host 127.0.0.1 --port 8000

worker:
	python -m app.worker

test:
	pytest
