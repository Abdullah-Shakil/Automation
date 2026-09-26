.PHONY: web test

web:
	uvicorn app.main:app --host 127.0.0.1 --port 8000

test:
	pytest
