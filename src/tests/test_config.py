import logging
import pytest
import os

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

TEST_DATABASE_URL = "sqlite:///test.db"
TEST_API_KEY = "test_key_123"


def test_config_values():
    assert TEST_DATABASE_URL.startswith("sqlite:///")
    assert TEST_API_KEY != ""


def test_env_loading(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///tmp/test.db")
    assert os.getenv("TEST_DATABASE_URL").startswith("sqlite:///")
