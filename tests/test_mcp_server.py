"""Tests for MCP server tool registration."""

import asyncio
from pathlib import Path

from mcp.server import MCPServer
from pydantic import BaseModel

from mcp_server.server import _register_app_resource, mcp
from services import get_registry
from tests.test_template import TestTemplate


class TestMCPServer(TestTemplate):
    def test_server_imports(self):
        assert mcp is not None

    def test_all_services_registered_as_tools(self):
        # Importing mcp_server.server (at module top) triggers service module
        # imports + registration. Some services are deliberately not exposed
        # through the default MCP surface, but they remain available to
        # CLI/API transports.
        registry = get_registry()
        service_names = {entry.name for entry in registry}

        assert "greet" in service_names
        assert "config_show" in service_names
        assert "doctor" in service_names

    def test_registry_entries_have_models(self):
        for entry in get_registry():
            assert entry.input_model is not None
            assert entry.output_model is not None
            assert entry.func is not None
            assert entry.description

    def test_default_mcp_surface_excludes_admin_and_demo_tools(self):
        tools = mcp._tool_manager._tools
        for tool_name in ("config_get", "config_set", "config_show", "doctor", "greet"):
            assert tool_name not in tools

    def test_default_factory_fields_resolve_to_concrete_defaults(self):
        # Regression: inspect.signature() renders a Pydantic default_factory
        # field with the private <factory> sentinel. FastMCP copies each
        # parameter default verbatim into the tool's argument model, so an
        # unresolved sentinel is handed back as the value when the caller omits
        # the field, failing validation ("Input should be a valid list ...
        # input_value=<factory>") and making the field effectively required.
        # _apply_tool_signature must resolve the factory to a concrete default.
        for tool_name, tool in mcp._tool_manager._tools.items():
            arg_model = tool.fn_metadata.arg_model
            for name, field in arg_model.model_fields.items():
                default_repr = repr(field.default)
                assert "<factory>" not in default_repr, (
                    f"{tool_name}.{name} leaks the default_factory sentinel "
                    f"into its tool schema: {default_repr}"
                )

    def test_resolve_signature_default_covers_all_factory_forms(self):
        # Unit-covers _resolve_signature_default across every branch, including
        # the validated-data factory form no shipped input model exercises yet.
        import inspect  # noqa: PLC0415

        from pydantic import Field  # noqa: PLC0415

        from mcp_server._tool_factory import _resolve_signature_default  # noqa: PLC0415

        class _Model(BaseModel):
            plain: str = "x"
            zero: list[str] = Field(default_factory=list)
            validated: str = Field(default_factory=lambda data: "derived")

        params = inspect.signature(_Model).parameters
        fields = _Model.model_fields

        # No FieldInfo, or a field without a factory -> keep the signature default.
        assert _resolve_signature_default(params["plain"], None) == "x"
        assert _resolve_signature_default(params["plain"], fields["plain"]) == "x"

        # Zero-arg factory resolves and yields a fresh object each call.
        first = _resolve_signature_default(params["zero"], fields["zero"])
        second = _resolve_signature_default(params["zero"], fields["zero"])
        first.append("mutated")
        assert first == ["mutated"] and second == []

        # Validated-data factory form is dispatched by arity, not a caught error.
        assert (
            _resolve_signature_default(params["validated"], fields["validated"])
            == "derived"
        )


class TestMCPServerIntegration(TestTemplate):
    """End-to-end integration tests calling tools through the registered FastMCP wrapper."""

    def test_missing_app_build_serves_stub_comment(self):
        # Edge case A1 (mcp_server/MCP_UI_EDGE_CASES.md): an app dir without a
        # built dist/mcp-app.html must serve an HTML comment stub, not crash.
        test_mcp = MCPServer("test_stub")
        missing = Path("/nonexistent/test_app/dist/mcp-app.html")
        _register_app_resource(
            test_mcp, "ui://telephone/test_stub_app", missing, "test_stub_app"
        )

        contents = list(
            asyncio.run(test_mcp.read_resource("ui://telephone/test_stub_app"))
        )
        assert len(contents) == 1
        first = contents[0]
        assert not isinstance(first, tuple)
        text = str(first.content)
        assert text.startswith("<!--")
        assert "test_stub_app not built" in text
        assert "make build_apps" in text
