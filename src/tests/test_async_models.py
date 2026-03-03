import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "config" / "async_database.py"


def test_async_models_file_exists_and_has_tables():
    assert SRC.exists(), "async_database.py must exist"
    src = SRC.read_text()
    # heuristic: count __tablename__ occurrences
    tablename_count = len(re.findall(r"__tablename__\s*=\s*[\"']\w+[\"']", src))
    assert tablename_count >= 27, f"expected >=27 __tablename__ declarations, found {tablename_count}"
    # presence of core tables
    assert "__tablename__ = \"users\"" in src
    assert "__tablename__ = \"parts_catalog\"" in src
    assert "__tablename__ = \"orders\"" in src
    assert "__tablename__ = \"files\"" in src


def test_async_database_has_async_engine_and_session():
    src = SRC.read_text()
    assert "create_async_engine" in src, "async engine must be configured"
    assert "async_sessionmaker" in src or "AsyncSessionLocal" in src


def test_model_class_names_present():
    src = SRC.read_text()
    for cls in ("User", "Order", "Conversation", "PartsCatalog", "Supplier", "File"):
        assert re.search(rf"class\s+{cls}\b", src), f"expected model class {cls} in async_database.py"
