import pathlib
import yaml


def test_docker_compose_has_required_services():
    p = pathlib.Path("docker-compose.yml")
    assert p.exists(), "docker-compose.yml must exist at repo root"
    doc = yaml.safe_load(p.read_text())
    assert isinstance(doc, dict)
    services = doc.get("services", {})
    assert "backend" in services, "backend service is required"
    assert "postgres" in services, "postgres service is required"
    backend = services["backend"]
    # minimal sanity checks
    assert ("command" in backend) or ("image" in backend)
    ports = backend.get("ports") or []
    assert any("8000" in str(p) for p in ports), "backend should expose port 8000"
