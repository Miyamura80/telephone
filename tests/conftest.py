# The live-server `server` fixture shared by the wire tests.
pytest_plugins = ["tests.wire_helpers"]


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "order: mark test to run in a specific order")
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end (requires live services)"
    )
