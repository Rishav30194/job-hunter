"""drop dead columns — company_health_score and recruiter_name

Revision ID: a3f9d2c1b4e7
Revises: 8c178ff41b9c
Create Date: 2026-06-08

"""
from alembic import op

revision = "a3f9d2c1b4e7"
down_revision = "8c178ff41b9c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("jobs", "company_health_score")
    op.drop_column("jobs", "recruiter_name")


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column("jobs", sa.Column("recruiter_name", sa.String(255), nullable=True))
    op.add_column("jobs", sa.Column("company_health_score", sa.Integer(), nullable=True))
