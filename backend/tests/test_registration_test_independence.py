"""#102 regression: the duplicate-registration tests must not depend on the registration test (or on test order).

They used to reuse the module's shared email (test_system._TEST_EMAIL / test_full_system._EMAIL): whichever ran
first created the user, so reversed/shuffled runs failed both tests (201 for the "duplicate", then 409 for the
"new" registration). These checks fail if either test goes back to the shared identity.
"""
import ast
import os

import pytest

import registered_user_cleanup as ruc

TESTS = os.path.dirname(os.path.abspath(__file__))
CASES = [("test_system.py", "test_auth_duplicate_register_rejected", {"_TEST_EMAIL", "_TEST_PHONE"}),
         ("test_full_system.py", "test_D_duplicate_register_rejected", {"_EMAIL", "_PHONE"})]


def _function(path, name):
    tree = ast.parse(open(os.path.join(TESTS, path), encoding="utf-8").read())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


@pytest.mark.parametrize("path,name,shared", CASES)
def test_duplicate_test_owns_its_identity(path, name, shared):
    fn = _function(path, name)
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert not names & shared, f"{name} uses the shared identity {sorted(names & shared)} again"
    assert "unique_identity" in names and "delete_registered_user" in names
    # it registers its own user first and asserts BOTH outcomes (the precondition and the 409)
    src = ast.get_source_segment(open(os.path.join(TESTS, path), encoding="utf-8").read(), fn)
    assert src.count("/api/v1/auth/register") == 2
    assert "status_code in (200, 201)" in src and "status_code == 409" in src and "Email already registered" in src
    # cleanup runs even when an assertion fails
    assert any(isinstance(n, ast.Try) and n.finalbody for n in ast.walk(fn))


def test_unique_identities_never_repeat_and_are_test_addresses():
    ids = [ruc.unique_identity("x") for _ in range(500)]
    emails, phones = zip(*ids)
    assert len(set(emails)) == 500
    assert all(e.endswith("@example.com") for e in emails)
    assert all(p.startswith("+9725") and len(p) == 12 and p[1:].isdigit() for p in phones)


def test_cleanup_refuses_a_non_test_address():
    with pytest.raises(ValueError):
        ruc.delete_registered_user("real.customer@gmail.com")


def test_cleanup_writes_nothing_when_the_pii_database_is_not_isolated(monkeypatch):
    import psycopg2

    def no_connect(*a, **k):
        raise AssertionError("must not connect")
    monkeypatch.setattr(psycopg2, "connect", no_connect)
    for env in ({"DATABASE_PII_URL": "postgresql+asyncpg://u:p@postgres_pii:5432/autospare_pii"},   # production host
                {"ENVIRONMENT": "production"}, {"RUN_DB_MUTATION_TESTS": "0"}, {"DATABASE_PII_URL": ""}):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert ruc.delete_registered_user("x_1@example.com").startswith("skipped:")
        monkeypatch.undo()
        monkeypatch.setattr(psycopg2, "connect", no_connect)


def test_register_test_owns_its_identity_too():
    """Second #102 mechanism (found by the release gate): test_auth_login_returns_token registers the shared user when
    it runs first, so the registration test must not use that identity either."""
    fn = _function("test_system.py", "test_auth_register_new_user")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert not names & {"_TEST_EMAIL", "_TEST_PHONE"}, sorted(names & {"_TEST_EMAIL", "_TEST_PHONE"})
    assert {"unique_identity", "delete_registered_user"} <= names
    assert any(isinstance(n, ast.Try) and n.finalbody for n in ast.walk(fn))
