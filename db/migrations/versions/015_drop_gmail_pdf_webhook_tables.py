"""Drop the Gmail, PDF-signing, and outbound-webhook tables.

Telephone does not ship the template's Gmail / PDF / webhook example domains,
so the tables created by revisions 007 and 009-013 are dropped here. History is
kept intact so databases created from the template upgrade cleanly.

Revision ID: 015
Revises: 014
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children before parents so foreign keys never block a drop.
_TABLES = (
    "webhook_deliveries",
    "webhook_events",
    "webhook_subscriptions",
    "processed_pubsub_messages",
    "thread_curation",
    "pdf_documents",
    "google_tokens",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    raise NotImplementedError(
        "015 is one-way: the dropped tables belong to removed features."
    )
