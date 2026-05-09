import os
import re


BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
REPO_DIR = os.path.dirname(BACKEND_DIR)
COMPOSE_PATH = os.path.join(REPO_DIR, "docker-compose.yml")


def _compose_text() -> str:
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return f.read()


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = rf"(?ms)^  {re.escape(service_name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)"
    match = re.search(pattern, compose_text)
    assert match, f"Service '{service_name}' not found in docker-compose.yml"
    return match.group(1)


def _assert_loopback_binding(service_block: str, expected: str) -> None:
    assert re.search(r"(?m)^\s*ports:\s*$", service_block), "Missing ports section"
    assert re.search(
        rf"(?m)^\s*-\s*[\"']?{re.escape(expected)}[\"']?\s*$",
        service_block,
    ), f"Expected loopback-only mapping '{expected}'"
    assert not re.search(r"(?m)^\s*-\s*[\"']?5432:5432[\"']?\s*$", service_block)
    assert not re.search(r"(?m)^\s*-\s*[\"']?5433:5432[\"']?\s*$", service_block)
    assert "0.0.0.0:" not in service_block


def test_postgres_ports_are_not_publicly_exposed() -> None:
    compose = _compose_text()

    catalog_block = _service_block(compose, "postgres_catalog")
    pii_block = _service_block(compose, "postgres_pii")

    _assert_loopback_binding(catalog_block, "127.0.0.1:5432:5432")
    _assert_loopback_binding(pii_block, "127.0.0.1:5433:5432")
