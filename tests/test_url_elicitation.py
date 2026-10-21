"""Unit tests for the SEP-1036 connect-required -> URL elicitation conversion.

Wire-level coverage (the -32042 JSON-RPC error as serialized over streamable
HTTP) lives in ``tests/test_mcp_e2e.py``; this module covers the conversion
policy in ``mcp_server/url_elicitation.py`` directly, using a fake
``ConnectRequiredError`` subclass as the concrete integration.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.session import ServerSession
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp.types import (
    URL_ELICITATION_REQUIRED,
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    UrlElicitationCapability,
)

from mcp_server.url_elicitation import _client_url_support, reraise_with_elicitation
from services import ConnectRequiredError
from tests.test_template import TestTemplate


def _session_with(capabilities: ClientCapabilities | None) -> ServerSession:
    """Fake ServerSession; only ``.client_params`` is read by the code under test."""
    if capabilities is None:
        fake = SimpleNamespace(client_params=None)
    else:
        fake = SimpleNamespace(client_params=SimpleNamespace(capabilities=capabilities))
    return cast(ServerSession, fake)


class FakeNotConnectedError(ConnectRequiredError):
    """Stand-in integration: configured unless ``auth_base`` is None."""

    auth_base: str | None = "https://auth.example.com/authorize"

    def __init__(self, user_id: str) -> None:
        super().__init__(
            user_id,
            "Account not connected. Call fake_connect, open the URL, then retry.",
            elicitation_message="Connect your Fake account to continue.",
        )

    def build_auth_url(self) -> str | None:
        if self.auth_base is None:
            return None
        return f"{self.auth_base}?state=signed-{self.user_id}"


def _unconfigured():
    return patch.object(FakeNotConnectedError, "auth_base", None)


class TestConnectRequiredContract(TestTemplate):
    def test_fake_error_implements_the_contract(self):
        exc = FakeNotConnectedError("user-1")
        assert isinstance(exc, ConnectRequiredError)
        assert exc.user_id == "user-1"
        assert "Fake" in exc.elicitation_message

    def test_auth_url_none_when_unconfigured(self):
        with _unconfigured():
            assert FakeNotConnectedError("user-2").build_auth_url() is None

    def test_auth_url_minted_when_configured(self):
        url = FakeNotConnectedError("user-3").build_auth_url()
        assert url is not None
        assert urlparse(url).netloc == "auth.example.com"


class TestClientUrlSupport(TestTemplate):
    def test_unknown_when_no_client_params(self):
        # The production stateless-HTTP case: initialize never reached this
        # transport, so capabilities are unknowable.
        assert _client_url_support(_session_with(None)) is None

    def test_unknown_when_no_session(self):
        # Direct in-process invocation (no MCP request context at all).
        assert _client_url_support(None) is None

    def test_false_when_client_declares_no_elicitation(self):
        session = _session_with(ClientCapabilities())
        assert _client_url_support(session) is False

    def test_false_when_client_declares_form_only(self):
        # Spec back-compat: `elicitation: {}` and form-only both mean no url.
        for cap in (
            ElicitationCapability(),
            ElicitationCapability(form=FormElicitationCapability()),
        ):
            session = _session_with(ClientCapabilities(elicitation=cap))
            assert _client_url_support(session) is False

    def test_true_when_client_declares_url(self):
        cap = ElicitationCapability(url=UrlElicitationCapability())
        session = _session_with(ClientCapabilities(elicitation=cap))
        assert _client_url_support(session) is True


class TestReraiseWithElicitation(TestTemplate):
    def test_converts_when_client_declares_url(self):
        cap = ElicitationCapability(url=UrlElicitationCapability())
        session = _session_with(ClientCapabilities(elicitation=cap))
        exc = FakeNotConnectedError("user-7")
        with pytest.raises(UrlElicitationRequiredError) as excinfo:
            reraise_with_elicitation(session, exc)

        err = excinfo.value.error
        assert err.code == URL_ELICITATION_REQUIRED == -32042
        elicitations = excinfo.value.elicitations
        assert len(elicitations) == 1
        elic = elicitations[0]
        assert elic.mode == "url"
        assert elic.elicitation_id.startswith("connect-")
        assert elic.message == exc.elicitation_message

        parsed = urlparse(elic.url)
        assert parsed.netloc == "auth.example.com"
        # Identity binding per the spec: the signed state in the auth URL
        # carries the user, not any client-supplied value.
        assert parse_qs(parsed.query)["state"]

        # The top-level message must keep naive hosts (no -32042 support)
        # self-recovering: original recovery script + the raw auth URL.
        assert "fake_connect" in err.message
        assert elic.url in err.message
        assert excinfo.value.__cause__ is exc

    def test_converts_when_capabilities_unknown(self):
        exc = FakeNotConnectedError("user-8")
        with pytest.raises(UrlElicitationRequiredError):
            reraise_with_elicitation(_session_with(None), exc)

    def test_falls_back_to_text_when_client_declares_form_only(self):
        session = _session_with(ClientCapabilities(elicitation=ElicitationCapability()))
        exc = FakeNotConnectedError("user-9")
        with pytest.raises(ToolError) as excinfo:
            reraise_with_elicitation(session, exc)
        # The model reads the self-recovering message; the cause is kept.
        assert str(excinfo.value) == str(exc)
        assert excinfo.value.__cause__ is exc

    def test_falls_back_to_text_when_unconfigured(self):
        exc = FakeNotConnectedError("user-10")
        with (
            _unconfigured(),
            pytest.raises(ToolError) as excinfo,
        ):
            reraise_with_elicitation(_session_with(None), exc)
        assert excinfo.value.__cause__ is exc

    def test_elicitation_ids_are_unique(self):
        exc = FakeNotConnectedError("user-11")
        ids = set()
        for _ in range(3):
            with pytest.raises(UrlElicitationRequiredError) as excinfo:
                reraise_with_elicitation(_session_with(None), exc)
            ids.add(excinfo.value.elicitations[0].elicitation_id)
        assert len(ids) == 3
