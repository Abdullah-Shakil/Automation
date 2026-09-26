import os
import tempfile
from pathlib import Path

_dir = Path(tempfile.mkdtemp(prefix="leadlane-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_dir / 'test.db'}"
os.environ["SECRET_KEY"] = "test-secret-key-not-default"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-password"
os.environ["ENVIRONMENT"] = "development"
os.environ["WORKER_POLL_SECONDS"] = "2"
os.environ["GOOGLE_PLACES_ENABLED"] = "false"
os.environ["COMPANIES_HOUSE_API_KEY"] = ""
os.environ["GOOGLE_PLACES_API_KEY"] = ""
os.environ["DIRECTORY_SEARCH_URL_TEMPLATE"] = ""

import pytest

from app.db import reset_database


@pytest.fixture(autouse=True)
def clean_database():
    reset_database()
    yield
