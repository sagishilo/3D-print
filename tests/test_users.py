"""Users and roles (gateway/app/auth.py, users.py; migration 012): sign-in per user, sessions that end when access
changes, role checks on every kind of endpoint and command, owner-only user management and the audit log."""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
pytest.importorskip("argon2")
pytest.importorskip("pydantic_settings")

from gateway_support import PASSWORD, add_user, csrf, gateway_client, login  # noqa: E402

auth = importlib.import_module("app.auth")
main = importlib.import_module("app.main")
commands = importlib.import_module("app.commands")


def sign_in(c, username: str, password: str = PASSWORD):
    return c.post("/api/v1/auth/login", json={"username": username, "password": password})


def make(c, username: str, role: str, **kw) -> dict:
    """Add a user to the running app's store (same event loop as the app: via the portal)."""
    return c.portal.call(add_user, c.app, username, role, kw.get("password", PASSWORD), kw.get("must_change", False))


@pytest.fixture
def c():
    with gateway_client() as client:
        yield client


# ---------------------------------------------------------------- sign in
def test_the_env_admin_becomes_the_first_owner(c):
    r = login(c)
    assert r.status_code == 200
    me = c.get("/api/v1/auth/me").json()
    assert me["user"] == "admin" and me["role"] == "owner" and me["must_change_password"] is False
    assert c.portal.call(c.app.state.users.count) == 1


def test_bootstrap_only_when_there_are_no_users(c):
    users = importlib.import_module("app.users")
    assert c.portal.call(users.bootstrap, c.app.state.users, "someone", "x") is None


def test_sign_in_by_username_any_case(c):
    make(c, "Noa", "operator")
    assert sign_in(c, "noa").status_code == 200
    me = c.get("/api/v1/auth/me").json()
    assert me["user"] == "Noa" and me["role"] == "operator" and me["display_name"] == "Noa"


def test_wrong_password_and_unknown_user_look_the_same(c):
    make(c, "noa", "operator")
    a, b = sign_in(c, "noa", "wrong password!"), sign_in(c, "nobody", "wrong password!")
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"].startswith("wrong username or password")
    assert b.json()["detail"].startswith("wrong username or password")


def test_a_disabled_user_cannot_sign_in(c):
    u = make(c, "noa", "operator")
    c.portal.call(lambda: c.app.state.users.update(u["id"], disabled=True))
    assert sign_in(c, "noa").status_code == 401


def test_lockout_is_per_username_too(c, monkeypatch):
    make(c, "noa", "operator")
    monkeypatch.setattr(auth.settings, "login_max_failures", 3)
    for _ in range(3):
        assert sign_in(c, "noa", "wrong password!").status_code == 401
    # the right password is refused while the username is locked, from any address
    auth._failures.pop("ip:testclient", None)
    assert sign_in(c, "noa").status_code == 429
    assert sign_in(c, "admin").status_code == 200       # other users are not locked


def test_old_sessions_without_a_user_belong_to_the_bootstrap_owner(c):
    login(c)
    c.app.state.db.tokens.add("legacy")    # FakeDb's refresh row has no user_id, as before migration 012
    r = c.post("/api/v1/auth/refresh")
    assert r.status_code == 200 and r.json()["user"] == "admin" and r.json()["role"] == "owner"


# ---------------------------------------------------------------- sessions end when access changes
def test_disabling_or_demoting_ends_the_session_at_once(c):
    u = make(c, "noa", "operator")
    assert sign_in(c, "noa").status_code == 200
    assert c.get("/api/v1/orders").status_code == 200
    c.portal.call(lambda: c.app.state.users.update(u["id"], session_version=2, disabled=True))
    auth.forget(u["id"])
    assert c.get("/api/v1/orders").status_code == 401


def test_owner_changes_reach_the_other_session(c):
    noa = make(c, "noa", "operator")
    with _second(c) as other:
        assert sign_in(other, "noa").status_code == 200
        login(c)
        r = c.patch(f"/api/v1/users/{noa['id']}", json={"role": "viewer"}, headers=csrf(c))
        assert r.status_code == 200 and r.json()["user"]["role"] == "viewer"
        assert other.get("/api/v1/orders").status_code == 401         # signed out: the role changed
        assert noa["id"] in c.app.state.users.revoked


class _second:
    """A second browser on the same app (its own cookies)."""

    def __init__(self, c):
        from fastapi.testclient import TestClient
        self.client = TestClient(c.app)

    def __enter__(self):
        return self.client

    def __exit__(self, *exc):
        self.client.close()


# ---------------------------------------------------------------- temporary passwords
def test_a_temporary_password_must_be_replaced_first(c):
    make(c, "noa", "operator", must_change=True)
    me = sign_in(c, "noa").json()
    assert me["must_change_password"] is True
    assert c.get("/api/v1/orders").json()["detail"] == "password change required"
    assert c.get("/api/v1/auth/me").status_code == 200
    bad = c.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "short"}, headers=csrf(c))
    assert bad.status_code == 422 and "12" in bad.json()["detail"]
    wrong = c.post("/api/v1/auth/password", json={"current_password": "nope", "new_password": "a much better passphrase"},
                   headers=csrf(c))
    assert wrong.status_code == 400
    ok = c.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "a much better passphrase"},
                headers=csrf(c))
    assert ok.status_code == 200 and ok.json()["must_change_password"] is False
    assert c.get("/api/v1/orders").status_code == 200                   # the fresh session works
    c.cookies.clear()
    assert sign_in(c, "noa", "a much better passphrase").status_code == 200


def test_password_rules():
    assert auth.password_problem("short") and auth.password_problem("aaaaaaaaaaaaaa")
    assert auth.password_problem(" padded password ") and auth.password_problem("correct horse battery staple") is None
    t = auth.temporary_password()
    assert len(t) == 19 and t.count("-") == 3 and not set(t) & set("0O1lI")


# ---------------------------------------------------------------- roles on endpoints and commands
@pytest.mark.parametrize("role, path, allowed", [
    ("viewer", "/api/v1/orders", True),
    ("viewer", "/api/v1/printers", True),
    ("viewer", "/api/v1/finance/report", False),
    ("operator", "/api/v1/finance/report", True),
    ("operator", "/api/v1/users", False),
    ("operator", "/api/v1/system/audit", False),
    ("owner", "/api/v1/users", True),
    ("owner", "/api/v1/system/audit", True),
])
def test_reads_by_role(c, role, path, allowed):
    make(c, f"u-{role}", role)
    sign_in(c, f"u-{role}")
    r = c.get(path)
    assert (r.status_code != 403) == allowed, (role, path, r.status_code, r.text)


@pytest.mark.parametrize("role, name, body, allowed", [
    ("viewer", "orders.create", {"customer_name": "x", "items": [{"text": "x", "quantity": 1, "unit_price_agorot": 100}]}, False),
    ("operator", "orders.create", {"customer_name": "x", "items": [{"text": "x", "quantity": 1, "unit_price_agorot": 100}]}, True),
    ("operator", "config.set", {"key": "orders.auto_personalize", "value": True}, False),
    ("owner", "config.set", {"key": "orders.auto_personalize", "value": True}, True),
    ("operator", "finance.settings", {"vat_rate": 0.18}, False),
    ("operator", "payments.refund", {"order_id": "ord-1", "amount_agorot": 100, "reason": "x"}, False),
])
def test_commands_by_role(c, role, name, body, allowed):
    make(c, f"u-{role}", role)
    sign_in(c, f"u-{role}")
    r = c.post(f"/api/v1/commands/{name}", json=body, headers=csrf(c))
    assert (r.status_code != 403) == allowed, (role, name, r.status_code, r.text)
    if not allowed:
        assert "needs" in r.json()["detail"]


def test_owner_only_commands_are_the_sensitive_ones():
    owner_only = {n for n, s in commands.COMMANDS.items() if s.min_role == "owner"}
    assert owner_only == {"config.set", "finance.settings", "payments.refund", "catalog.niche.upsert", "catalog.niche.archive",
                          "analytics.backfill", "integrations.configure", "integrations.run"}
    assert all(s.min_role in auth.ROLE_RANK for s in commands.COMMANDS.values())


@pytest.mark.parametrize("role, method, path, allowed", [
    ("viewer", "post", "/api/v1/commands/printers/p1/pause", False),
    ("operator", "post", "/api/v1/commands/printers/p1/pause", True),
    ("viewer", "post", "/api/v1/catalog/uploads", False),
    ("operator", "post", "/api/v1/social/tokens", False),
    ("operator", "post", "/api/v1/system/dlq/1/discard", False),
    ("viewer", "post", "/api/v1/printers/test-connection", False),
])
def test_other_changes_by_role(c, role, method, path, allowed):
    make(c, f"u-{role}", role)
    sign_in(c, f"u-{role}")
    r = getattr(c, method)(path, json={}, headers=csrf(c))
    assert (r.status_code != 403) == allowed, (role, path, r.status_code, r.text)


def test_every_change_route_checks_a_role():
    """Routes that change something use require_csrf at least; the role-limited ones are named here, so a new route
    cannot slip in without deciding who may use it."""
    from fastapi.routing import APIRoute

    unguarded = []
    for r in main.app.routes:
        if not isinstance(r, APIRoute) or not ({"POST", "PUT", "PATCH", "DELETE"} & r.methods):
            continue
        if r.path.startswith("/api/v1/auth/") or r.path in ("/api/v1/social/captures",):
            continue
        deps = {getattr(d.call, "__qualname__", "") for d in r.dependant.dependencies}
        if not deps & {"require_csrf", "require_csrf_role.<locals>.dep"}:
            unguarded.append(r.path)
    assert unguarded == []


# ---------------------------------------------------------------- user management (owner)
def test_owner_adds_a_user_with_a_one_time_password(c):
    login(c)
    r = c.post("/api/v1/users", json={"username": "dana", "display_name": "Dana", "role": "operator"}, headers=csrf(c))
    assert r.status_code == 201
    temp = r.json()["temporary_password"]
    assert r.json()["user"]["must_change_password"] is True and "password_hash" not in r.json()["user"]
    assert c.post("/api/v1/users", json={"username": "DANA", "role": "viewer"}, headers=csrf(c)).status_code == 409
    assert c.post("/api/v1/users", json={"username": "a b", "role": "viewer"}, headers=csrf(c)).status_code == 422
    assert c.post("/api/v1/users", json={"username": "kim", "role": "boss"}, headers=csrf(c)).status_code == 422
    c.cookies.clear()
    assert sign_in(c, "dana", temp).json()["must_change_password"] is True
    audit = [a for a in c.app.state.db.audit.values() if a["subject"] == "users.create"]
    assert audit and audit[0]["payload"] == {"user": "dana", "role": "operator"}
    assert temp not in repr(c.app.state.db.audit)


def test_reset_password_ends_their_sessions(c):
    noa = make(c, "noa", "operator")
    login(c)
    r = c.post(f"/api/v1/users/{noa['id']}/reset-password", headers=csrf(c))
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    assert noa["id"] in c.app.state.users.revoked
    assert r.json()["temporary_password"] not in repr(c.app.state.db.audit)
    me = c.portal.call(c.app.state.users.get, 1)
    assert c.post(f"/api/v1/users/{me['id']}/reset-password", headers=csrf(c)).status_code == 409   # not your own


def test_the_last_owner_stays(c):
    login(c)
    me = c.portal.call(c.app.state.users.by_username, "admin")
    assert c.patch(f"/api/v1/users/{me['id']}", json={"role": "operator"}, headers=csrf(c)).status_code == 409
    other = make(c, "boss", "owner")
    # another owner can be demoted while one remains, but you cannot demote or disable yourself
    assert c.patch(f"/api/v1/users/{other['id']}", json={"disabled": True}, headers=csrf(c)).status_code == 200
    assert c.patch(f"/api/v1/users/{me['id']}", json={"disabled": True}, headers=csrf(c)).status_code == 409
    upd = c.patch(f"/api/v1/users/{other['id']}", json={"disabled": False, "display_name": "Big boss"}, headers=csrf(c))
    assert upd.json()["user"]["display_name"] == "Big boss" and upd.json()["user"]["disabled"] is False
    audit = [a for a in c.app.state.db.audit.values() if a["subject"] == "users.update"]
    assert audit[-1]["payload"]["was"] == {"disabled": True, "display_name": "Boss"}


def test_users_list_never_shows_password_hashes(c):
    login(c)
    users = c.get("/api/v1/users").json()["users"]
    assert users and all("password_hash" not in u for u in users)


# ---------------------------------------------------------------- audit log
def test_audit_filters_and_pages(c):
    login(c)
    db = c.app.state.db
    seen = {}

    async def fetchall(sql, params=()):
        seen["sql"], seen["params"] = sql, params
        return [{"id": i, "created_at": "2026-11-19T10:00:00Z", "actor": "dana", "subject": "users.create",
                 "outcome": "done", "payload": {}} for i in (9, 8, 7)]

    db.fetchall = fetchall
    r = c.get("/api/v1/system/audit?actor=dana&action=users&since=2026-11-01&until=2026-11-19&limit=2").json()
    assert [e["id"] for e in r["entries"]] == [9, 8] and r["next_before_id"] == 8
    assert "actor = %s" in seen["sql"] and "subject LIKE %s" in seen["sql"] and "created_at <" in seen["sql"]
    assert seen["params"] == ("dana", "users%", "2026-11-01", "2026-11-19", 3)
    assert c.get("/api/v1/system/audit?since=yesterday").status_code == 400
    c.get("/api/v1/system/audit?action=cmd_x&before_id=50&outcome=failed")
    assert seen["params"][0] == "cmd\\_x%" and "id < %s" in seen["sql"] and "failed" in seen["sql"]


# ---------------------------------------------------------------- console recovery
def test_recovery_makes_an_enabled_owner_with_a_one_time_password(c):
    recover = importlib.import_module("app.recover")
    noa = make(c, "noa", "viewer")
    c.portal.call(lambda: c.app.state.users.update(noa["id"], disabled=True))
    temp = c.portal.call(recover.recover, c.app.state.users, c.app.state.db, "NOA")
    row = c.portal.call(c.app.state.users.get, noa["id"])
    assert row["role"] == "owner" and not row["disabled"] and row["must_change_password"]
    assert noa["id"] in c.app.state.users.revoked
    assert sign_in(c, "noa", temp).json()["must_change_password"] is True
    audit = [a for a in c.app.state.db.audit.values() if a["subject"] == "users.recover"]
    assert audit and audit[0]["payload"] == {"user": "noa"} and temp not in repr(c.app.state.db.audit)
    with pytest.raises(LookupError):
        c.portal.call(recover.recover, c.app.state.users, c.app.state.db, "ghost")


# ---------------------------------------------------------------- display preferences (M2)
def test_prefs_are_saved_per_person_and_merged(c):
    make(c, "noa", "viewer")
    sign_in(c, "noa")
    r = c.patch("/api/v1/auth/prefs", json={"theme": "light"}, headers=csrf(c))
    assert r.status_code == 200 and r.json()["prefs"] == {"theme": "light"}
    r = c.patch("/api/v1/auth/prefs", json={"density": "compact", "mode": "expert"}, headers=csrf(c))
    assert r.json()["prefs"] == {"theme": "light", "density": "compact", "mode": "expert"}   # viewers may choose too
    assert c.get("/api/v1/auth/me").json()["prefs"]["density"] == "compact"
    assert c.patch("/api/v1/auth/prefs", json={"theme": "pink"}, headers=csrf(c)).status_code == 422
    assert c.patch("/api/v1/auth/prefs", json={"role": "owner"}, headers=csrf(c)).status_code == 422
    assert c.patch("/api/v1/auth/prefs", json={"theme": "dark"}).status_code == 403          # CSRF
    # another person's prefs are untouched
    c.cookies.clear()
    login(c)
    assert c.get("/api/v1/auth/me").json()["prefs"] == {}
