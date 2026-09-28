"""Initial schema — all ushanr tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-26
"""
from __future__ import annotations

from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # companies
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(255)),
        sa.Column("trade_name", sa.String(255)),
        sa.Column("tax_id", sa.String(100), unique=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="KW"),
        sa.Column("address", sa.Text()),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("website", sa.String(255)),
        sa.Column("logo_url", sa.String(500)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("fiscal_year_start_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decimal_places", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # currencies
    op.create_table(
        "currencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(3), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(10)),
        sa.Column("decimal_places", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_base", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # currency_rates
    op.create_table(
        "currency_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("inverse_rate", sa.Numeric(20, 10)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("currency_code", "rate_date", name="uq_currency_rate_date"),
    )

    # account_groups
    op.create_table(
        "account_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "code", name="uq_account_group_company_code"),
        sa.ForeignKeyConstraint(["parent_id"], ["account_groups.id"], ondelete="SET NULL"),
    )

    # accounts (chart of accounts)
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("account_nature", sa.String(10), nullable=False, server_default="debit"),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("group_id", postgresql.UUID(as_uuid=True)),
        sa.Column("is_reconcilable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_bank_account", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allow_reconciliation", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("currency_code", sa.String(3)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text()),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("opening_debit", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("opening_credit", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "code", name="uq_account_company_code"),
        sa.ForeignKeyConstraint(["parent_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["group_id"], ["account_groups.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_accounts_company_id", "accounts", ["company_id"])
    op.create_index("ix_accounts_code", "accounts", ["code"])
    op.create_index("ix_accounts_account_type", "accounts", ["account_type"])

    # partners
    op.create_table(
        "partners",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("partner_type", sa.String(20), nullable=False, server_default="customer"),
        sa.Column("is_customer", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_vendor", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_company", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(50)),
        sa.Column("mobile", sa.String(50)),
        sa.Column("website", sa.String(255)),
        sa.Column("street", sa.String(255)),
        sa.Column("city", sa.String(100)),
        sa.Column("state", sa.String(100)),
        sa.Column("zip_code", sa.String(20)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("tax_id", sa.String(100)),
        sa.Column("vat_number", sa.String(100)),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credit_limit", sa.Numeric(20, 3)),
        sa.Column("external_id", sa.String(255)),
        sa.Column("receivable_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payable_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("notes", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["receivable_account_id"], ["accounts.id"],
            use_alter=True, name="fk_partner_receivable_account", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["payable_account_id"], ["accounts.id"],
            use_alter=True, name="fk_partner_payable_account", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_partners_company_id", "partners", ["company_id"])
    op.create_index("ix_partners_name", "partners", ["name"])

    # journals
    op.create_table(
        "journals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("journal_type", sa.String(20), nullable=False),
        sa.Column("default_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("suspense_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payment_debit_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payment_credit_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sequence_prefix", sa.String(20)),
        sa.Column("sequence_padding", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("currency_code", sa.String(3)),
        sa.Column("restrict_mode_hash_table", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("show_on_dashboard", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("description", sa.Text()),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["default_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["suspense_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_debit_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_credit_account_id"], ["accounts.id"], ondelete="SET NULL"),
    )

    # fiscal_years
    op.create_table(
        "fiscal_years",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("closing_entry_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "name", name="uq_fiscal_year_company_name"),
    )

    # accounting_periods
    op.create_table(
        "accounting_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "date_from", "date_to", name="uq_period_dates"),
        sa.ForeignKeyConstraint(["fiscal_year_id"], ["fiscal_years.id"], ondelete="CASCADE"),
    )

    # analytic_plans
    op.create_table(
        "analytic_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50)),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("default_applicability", sa.Numeric(5, 2), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # analytic_accounts
    op.create_table(
        "analytic_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("code", sa.String(50)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plan_id"], ["analytic_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["analytic_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="SET NULL"),
    )

    # journal_entries
    op.create_table(
        "journal_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column("reference", sa.String(255)),
        sa.Column("narration", sa.Text()),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("accounting_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date()),
        sa.Column("period_id", postgresql.UUID(as_uuid=True)),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("posted_by", sa.String(255)),
        sa.Column("amount_total", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("reversed_entry_id", postgresql.UUID(as_uuid=True)),
        sa.Column("is_reversal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_document_type", sa.String(50)),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["period_id"], ["accounting_periods.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reversed_entry_id"], ["journal_entries.id"],
            use_alter=True, name="fk_je_reversed_entry", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_journal_entries_company_id", "journal_entries", ["company_id"])
    op.create_index("ix_journal_entries_state", "journal_entries", ["state"])
    op.create_index("ix_journal_entries_accounting_date", "journal_entries", ["accounting_date"])

    # journal_items
    op.create_table(
        "journal_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("analytic_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("debit_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("credit_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("amount_currency", sa.Numeric(20, 6)),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("currency_rate", sa.Numeric(20, 10)),
        sa.Column("reconciled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reconcile_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(500)),
        sa.Column("reference", sa.String(255)),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["entry_id"], ["journal_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["analytic_account_id"], ["analytic_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_journal_items_entry_id", "journal_items", ["entry_id"])
    op.create_index("ix_journal_items_account_id", "journal_items", ["account_id"])

    # tax_groups
    op.create_table(
        "tax_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # taxes
    op.create_table(
        "taxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tax_type", sa.String(20), nullable=False),
        sa.Column("computation", sa.String(20), nullable=False, server_default="percentage"),
        sa.Column("amount", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("tax_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("tax_refund_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("group_id", postgresql.UUID(as_uuid=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("include_in_price", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tax_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tax_refund_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["group_id"], ["tax_groups.id"], ondelete="SET NULL"),
    )

    # invoices
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), unique=True),
        sa.Column("reference", sa.String(255)),
        sa.Column("invoice_type", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_entry_id", postgresql.UUID(as_uuid=True)),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date()),
        sa.Column("accounting_date", sa.Date()),
        sa.Column("payment_terms", sa.String(20), nullable=False, server_default="immediate"),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("amount_untaxed", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("amount_tax", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("amount_total", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("amount_paid", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("amount_residual", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("reversed_invoice_id", postgresql.UUID(as_uuid=True)),
        sa.Column("is_reversal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_document_type", sa.String(50)),
        sa.Column("source_document_id", sa.String(255)),
        sa.Column("source_document_ref", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reversed_invoice_id"], ["invoices.id"],
            use_alter=True, name="fk_invoice_reversed", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_invoices_company_id", "invoices", ["company_id"])
    op.create_index("ix_invoices_state", "invoices", ["state"])
    op.create_index("ix_invoices_invoice_date", "invoices", ["invoice_date"])

    # invoice_lines
    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytic_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("tax_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("discount", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("subtotal", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("product_id", sa.String(255)),
        sa.Column("product_code", sa.String(100)),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["analytic_account_id"], ["analytic_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tax_id"], ["taxes.id"], ondelete="SET NULL"),
    )

    # invoice_taxes
    op.create_table(
        "invoice_taxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_name", sa.String(255), nullable=False),
        sa.Column("base_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tax_id"], ["taxes.id"], ondelete="RESTRICT"),
    )

    # anr_payments
    op.create_table(
        "anr_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), unique=True),
        sa.Column("reference", sa.String(255)),
        sa.Column("payment_type", sa.String(20), nullable=False, server_default="inbound"),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("partner_name", sa.String(255)),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_entry_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 3), nullable=False),
        sa.Column("amount_residual", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("payment_method", sa.String(50)),
        sa.Column("payment_provider", sa.String(100)),
        sa.Column("payment_id_external", sa.String(255)),
        sa.Column("transaction_id", sa.String(255)),
        sa.Column("invoice_id_external", sa.String(255)),
        sa.Column("reference_id", sa.String(255)),
        sa.Column("track_id", sa.String(255)),
        sa.Column("payment_data", postgresql.JSONB()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_by_name", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_anr_payments_company_id", "anr_payments", ["company_id"])
    op.create_index("ix_anr_payments_state", "anr_payments", ["state"])
    op.create_index("ix_anr_payments_payment_date", "anr_payments", ["payment_date"])

    # payment_allocations
    op.create_table(
        "payment_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(20, 3), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["payment_id"], ["anr_payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
    )

    # analytic_items
    op.create_table(
        "analytic_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytic_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("partner_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(500)),
        sa.Column("date", sa.Date()),
        sa.Column("amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["analytic_account_id"], ["analytic_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["journal_item_id"], ["journal_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], ondelete="SET NULL"),
    )

    # bank_accounts
    op.create_table(
        "bank_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_id", postgresql.UUID(as_uuid=True)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("account_number", sa.String(100)),
        sa.Column("bank_name", sa.String(255)),
        sa.Column("bank_code", sa.String(50)),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("account_type", sa.String(10), nullable=False, server_default="bank"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="SET NULL"),
    )

    # bank_statements
    op.create_table(
        "bank_statements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("balance_start", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("balance_end", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="CASCADE"),
    )

    # bank_statement_lines
    op.create_table(
        "bank_statement_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("statement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("journal_entry_id", postgresql.UUID(as_uuid=True)),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(255)),
        sa.Column("amount", sa.Numeric(20, 3), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("is_reconciled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["statement_id"], ["bank_statements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"], ondelete="SET NULL"),
    )

    # budgets
    op.create_table(
        "budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True)),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["fiscal_year_id"], ["fiscal_years.id"], ondelete="SET NULL"),
    )

    # budget_lines
    op.create_table(
        "budget_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("budget_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analytic_account_id", postgresql.UUID(as_uuid=True)),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("planned_amount", sa.Numeric(20, 3), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="KWD"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["analytic_account_id"], ["analytic_accounts.id"], ondelete="SET NULL"),
    )

    # document_sequences
    op.create_table(
        "document_sequences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("prefix", sa.String(20)),
        sa.Column("suffix", sa.String(20)),
        sa.Column("next_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("padding", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("use_date_range", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("user_id", sa.String(255)),
        sa.Column("user_name", sa.String(255)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("old_values", postgresql.JSONB()),
        sa.Column("new_values", postgresql.JSONB()),
        sa.Column("description", sa.Text()),
        sa.Column("company_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
    op.create_index("ix_audit_logs_resource_id", "audit_logs", ["resource_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("document_sequences")
    op.drop_table("budget_lines")
    op.drop_table("budgets")
    op.drop_table("bank_statement_lines")
    op.drop_table("bank_statements")
    op.drop_table("bank_accounts")
    op.drop_table("analytic_items")
    op.drop_table("payment_allocations")
    op.drop_table("anr_payments")
    op.drop_table("invoice_taxes")
    op.drop_table("invoice_lines")
    op.drop_table("invoices")
    op.drop_table("taxes")
    op.drop_table("tax_groups")
    op.drop_table("journal_items")
    op.drop_table("journal_entries")
    op.drop_table("analytic_accounts")
    op.drop_table("analytic_plans")
    op.drop_table("accounting_periods")
    op.drop_table("fiscal_years")
    op.drop_table("journals")
    op.drop_table("partners")
    op.drop_table("accounts")
    op.drop_table("account_groups")
    op.drop_table("currency_rates")
    op.drop_table("currencies")
    op.drop_table("companies")
