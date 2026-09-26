import os
import tempfile
from pathlib import Path

_dir = Path(tempfile.mkdtemp(prefix="leadlane-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_dir / 'test.db'}"
os.environ["ENVIRONMENT"] = "development"
os.environ["WORKER_POLL_SECONDS"] = "2"
os.environ["COMPANIES_HOUSE_API_KEY"] = ""
# Never let a developer .env pull tests onto Supabase.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_DB_PASSWORD"] = ""
os.environ["SUPABASE_DB_REGION"] = ""
os.environ["SUPABASE_DB_POOLER_HOST"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""

import pytest

from app.db import reset_database


@pytest.fixture(autouse=True)
def clean_database():
    reset_database()
    yield
