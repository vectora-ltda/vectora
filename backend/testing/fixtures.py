import os
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest


@pytest.fixture
async def temp_db() -> AsyncGenerator[str]:
    """Create a temporary SQLite database for testing.

    Yields the file path (not DSN), cleans up after test.
    """
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"

    yield str(db_path)

    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def vector_store_dir() -> str:
    """Provide a temporary LanceDB directory for testing.

    LanceDB is used for vector storage in tests.
    This is automatically cleaned up after the test.
    """
    temp_dir = tempfile.mkdtemp()
    os.environ["LANCEDB_DIR"] = temp_dir
    return temp_dir
