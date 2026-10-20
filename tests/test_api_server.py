"""Integration tests for API server route registration."""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.server import app
from db.base import Base
from db.engine import get_db_session
from tests.test_template import TestTemplate

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_engine)
_SessionLocal = sessionmaker(bind=_engine)


@contextmanager
def _override_use_db_session():
    """Yield a session from the in-memory test engine for use_db_session."""
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _parse_sse(body: str) -> list[dict[str, str]]:
    """Parse an SSE body into a list of ``{"event", "data"}`` dicts."""
    events: list[dict[str, str]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                event["event"] = line[len("event:") :].strip()
            elif line.startswith("data:"):
                event["data"] = line[len("data:") :].strip()
        if event:
            events.append(event)
    return events


def _override_auth():
    return AuthenticatedUser(user_id="test-user", email="t@t.com", auth_method="jwt")


def _override_db():
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestAPIServer(TestTemplate):
    def setup_method(self):
        app.dependency_overrides[get_authenticated_user] = _override_auth
        app.dependency_overrides[get_db_session] = _override_db
        # Patch use_db_session so ensure_daily_limit uses the test DB
        self._use_db_patcher = patch(
            "api_server.billing.limits.use_db_session",
            _override_use_db_session,
        )
        self._use_db_patcher.start()
        self.client = TestClient(app)

    def teardown_method(self):
        self._use_db_patcher.stop()
        app.dependency_overrides.clear()

    def test_health_endpoint(self):
        # Public /health is a liveness signal only; the component breakdown
        # moved to the authenticated /health/detail (tests/test_health_enhanced.py).
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}

    def test_health_detail_endpoint(self):
        resp = self.client.get("/health/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert data["components"]["api"]["status"] == "ok"

    def test_greet_service(self):
        resp = self.client.post(
            "/api/v1/services/greet",
            json={"name": "World"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Hello, World!"

    def test_service_requires_auth(self):
        """Without auth override, endpoints should require credentials."""
        # Remove only the auth override; keep the DB override
        app.dependency_overrides.pop(get_authenticated_user, None)
        with patch("api_server.auth.workos_auth.global_config") as mock_config:
            mock_config.WORKOS_CLIENT_ID = None
            client = TestClient(app)
            resp = client.post(
                "/api/v1/services/greet",
                json={"name": "World"},
            )
            assert resp.status_code == 401

    def test_me_endpoint(self):
        resp = self.client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "test-user"
        assert data["auth_method"] == "jwt"

    def test_service_routes_registered(self):
        """All services should have corresponding API routes."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/services/greet" in routes
        assert "/api/v1/services/config_show" in routes

    def test_mutating_service_requires_idempotency_key(self):
        """config_set is mutating=True, so its route enforces Idempotency-Key."""
        resp = self.client.post(
            "/api/v1/services/config_set",
            json={"key": "cli.emoji", "value": "x"},
        )
        assert resp.status_code == 422
        assert "Idempotency-Key" in resp.json()["error"]["message"]

    def test_billing_routes_registered(self):
        """Billing routes should be registered."""
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/billing/checkout/create" in routes
        assert "/api/v1/billing/usage/current" in routes
        assert "/api/v1/billing/subscription/status" in routes

    def test_stream_doctor_route_registered(self):
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/v1/stream/doctor" in routes

    def test_stream_doctor_emits_sse_events(self):
        """The SSE endpoint streams one `check` event per check, then `done`."""
        with self.client.stream(
            "POST", "/api/v1/stream/doctor", json={"fix": False}
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())

        events = _parse_sse(body)
        checks = [e for e in events if e.get("event") == "check"]
        dones = [e for e in events if e.get("event") == "done"]

        assert len(checks) >= 1
        assert len(dones) == 1
        # Ordering contract: every check streams before the terminal `done`.
        assert events[-1]["event"] == "done"
        assert all(e["event"] == "check" for e in events[:-1])

        first = json.loads(checks[0]["data"])
        assert "name" in first
        assert first["status"] in ("pass", "fail", "warn")

        done = json.loads(dones[0]["data"])
        assert isinstance(done["has_failures"], bool)

    def test_stream_doctor_requires_execute_scope(self):
        """A read-only key may not open the doctor stream."""
        original = app.dependency_overrides.get(get_authenticated_user)
        try:
            app.dependency_overrides[get_authenticated_user] = lambda: (
                AuthenticatedUser(
                    user_id="scoped-user",
                    auth_method="api_key",
                    scopes=["services:read"],
                )
            )
            client = TestClient(app)
            resp = client.post("/api/v1/stream/doctor", json={"fix": False})
            assert resp.status_code == 403
        finally:
            if original is not None:
                app.dependency_overrides[get_authenticated_user] = original
            else:
                app.dependency_overrides.pop(get_authenticated_user, None)

    def test_403_on_insufficient_scopes(self):
        """A key with read-only scopes should be rejected from service execution."""
        # Override auth to return a user with limited scopes
        original = app.dependency_overrides.get(get_authenticated_user)
        try:
            app.dependency_overrides[get_authenticated_user] = lambda: (
                AuthenticatedUser(
                    user_id="scoped-user",
                    auth_method="api_key",
                    scopes=["services:read"],
                )
            )
            client = TestClient(app)
            resp = client.post("/api/v1/services/greet", json={"name": "World"})
            assert resp.status_code == 403
        finally:
            # Restore original override to prevent leaking into other tests
            if original is not None:
                app.dependency_overrides[get_authenticated_user] = original
            else:
                app.dependency_overrides.pop(get_authenticated_user, None)


class ServiceRouteIdempotencyBase(TestTemplate):
    """Shared harness proving a ``mutating=True`` service's auto-generated
    route enforces Idempotency-Key end-to-end against the real route factory +
    ``execute_idempotent`` (not a synthetic stand-in route).

    Subclasses set ``ROUTE`` and implement:

    - ``_payload()`` / ``_conflicting_payload()`` - request bodies (the second
      must hash differently for the conflict test);
    - ``_mutation_count()`` - how many times the underlying side effect has
      actually happened (DB rows written, files written, ...), so the
      generic tests can assert 0 mutations without a key and exactly 1 across
      an execute + replay pair;
    - ``_extra_setup()`` / ``_extra_patchers()`` / ``_extra_teardown()`` -
      service-specific mocks and overrides.

    The base provides the fresh in-memory DB, auth override, idempotency +
    billing session patches, and the missing-key / replay / conflict triad.
    """

    ROUTE: str

    def setup_method(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_authenticated_user] = _override_auth
        self._extra_setup()

        self._patchers = [
            patch("api_server.idempotency.use_db_session", _ctx),
            patch("api_server.billing.limits.use_db_session", _ctx),
            # Disable opportunistic idempotency-key cleanup so it can't race.
            patch("api_server.idempotency.random.random", return_value=1.0),
            *self._extra_patchers(),
        ]
        for p in self._patchers:
            p.start()
        self.client = TestClient(app)

    def teardown_method(self):
        for p in self._patchers:
            p.stop()
        self._extra_teardown()
        app.dependency_overrides.clear()

    # -- subclass hooks ----------------------------------------------------

    def _extra_setup(self) -> None:
        """Runs after the engine/auth override exist, before patchers start."""

    def _extra_patchers(self) -> list:
        return []

    def _extra_teardown(self) -> None:
        """Runs after patchers stop, before dependency overrides clear."""

    def _payload(self) -> dict:
        raise NotImplementedError

    def _conflicting_payload(self) -> dict:
        raise NotImplementedError

    def _mutation_count(self) -> int:
        raise NotImplementedError

    def _assert_first_response(self, resp) -> None:
        """Optional service-specific assertions on the first 200 response."""

    # -- generic Idempotency-Key triad -------------------------------------

    def test_missing_idempotency_key_422(self):
        """Without a key the route refuses - and the side effect never runs."""
        resp = self.client.post(self.ROUTE, json=self._payload())
        assert resp.status_code == 422
        assert "Idempotency-Key" in resp.json()["error"]["message"]
        assert self._mutation_count() == 0

    def test_replay_same_key_does_not_remutate(self):
        headers = {"Idempotency-Key": "k-replay"}
        first = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert first.status_code == 200, first.text
        self._assert_first_response(first)
        assert self._mutation_count() == 1
        # Retry with the same key: cached body replayed, no second side effect.
        second = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert second.status_code == 200
        assert second.json() == first.json()
        assert self._mutation_count() == 1

    def test_same_key_different_payload_conflicts(self):
        headers = {"Idempotency-Key": "k-conflict"}
        first = self.client.post(self.ROUTE, json=self._payload(), headers=headers)
        assert first.status_code == 200, first.text
        conflict = self.client.post(
            self.ROUTE, json=self._conflicting_payload(), headers=headers
        )
        assert conflict.status_code == 422


class TestConfigSetIdempotency(ServiceRouteIdempotencyBase):
    """config_set: the mutation is a write of the YAML override file - a
    retried request with the same key must replay instead of rewriting it."""

    ROUTE = "/api/v1/services/config_set"

    def _extra_setup(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dump = MagicMock(wraps=yaml.safe_dump)

    def _extra_patchers(self):
        return [
            patch("services.config_svc._ROOT_DIR", Path(self._tmp.name)),
            patch("services.config_svc.yaml.safe_dump", self._dump),
        ]

    def _extra_teardown(self):
        self._tmp.cleanup()

    def _payload(self) -> dict:
        return {"key": "cli.emoji", "value": "x"}

    def _conflicting_payload(self) -> dict:
        return {"key": "cli.emoji", "value": "y"}

    def _mutation_count(self) -> int:
        return self._dump.call_count

    def _assert_first_response(self, resp):
        assert resp.json()["coerced_value"] == "x"
