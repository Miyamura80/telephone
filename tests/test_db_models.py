"""Smoke tests for ORM model classes (no live DB required)."""

from sqlalchemy import create_engine

import db.models  # noqa: F401 - registers every model with Base.metadata
from db.base import Base
from tests.test_template import TestTemplate


class TestModelRegistry(TestTemplate):
    def test_core_tables_registered(self):
        tables = set(Base.metadata.tables)
        assert {"api_keys", "idempotency_keys", "profiles"}.issubset(tables)

    def test_removed_domain_tables_absent(self):
        tables = set(Base.metadata.tables)
        for removed in ("google_tokens", "webhook_subscriptions", "pdf_documents"):
            assert removed not in tables

    def test_can_create_in_sqlite(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
