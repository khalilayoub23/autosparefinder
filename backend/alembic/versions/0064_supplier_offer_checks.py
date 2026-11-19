"""supplier_offer_checks: the Listing Identity Guard's per-offer verdict (2026-11-19)

One row per supplier offer (latest verdict wins): why an eBay refresh did or did not write, and when to retry.
A NEW table (no FK, no change to supplier_parts) so creating it takes no lock on the 4M-row offer table.

Revision ID: 0064_supplier_offer_checks
Revises: 0063_importer_gap_partial_index
"""
from alembic import op

revision = "0064_supplier_offer_checks"
down_revision = "0063_importer_gap_partial_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS public.supplier_offer_checks ("
        " supplier_part_id uuid PRIMARY KEY,"
        " verdict text NOT NULL,"
        " reason text NOT NULL,"
        " live_item_id text,"
        " source text NOT NULL DEFAULT 'ebay_refresh',"
        " checked_at timestamptz NOT NULL DEFAULT now(),"
        " retry_after timestamptz NOT NULL,"
        " detail jsonb)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_supplier_offer_checks_verdict ON public.supplier_offer_checks (verdict, retry_after)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.supplier_offer_checks")
