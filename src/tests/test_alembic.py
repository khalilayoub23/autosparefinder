import pathlib


def test_initial_alembic_revision_exists():
    vdir = pathlib.Path("alembic/versions")
    assert vdir.exists(), "alembic/versions directory must exist"
    files = list(vdir.glob("*0001*_*.py")) + list(vdir.glob("*0001*.py"))
    assert any(files), "expected an initial migration file (0001) in alembic/versions"
    # quick content sanity
    content = files[0].read_text()
    assert "Revision ID: 0001_initial" in content or "revision = '0001_initial'" in content
