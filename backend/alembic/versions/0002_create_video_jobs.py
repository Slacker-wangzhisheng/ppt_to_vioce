"""创建 video_jobs 表

Revision ID: 0002_create_video_jobs
Revises: 0001_create_jobs
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_create_video_jobs"
down_revision = "0001_create_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("audio_job_id", sa.String(length=36), nullable=False),
        sa.Column("avatar_name", sa.String(length=255), nullable=False),
        sa.Column("avatar_path", sa.String(length=255), nullable=False),
        sa.Column("video_path", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audio_job_id"], ["jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_video_jobs_audio_job_id", "video_jobs", ["audio_job_id"], unique=False
    )
    op.create_index("ix_video_jobs_status", "video_jobs", ["status"], unique=False)
    op.create_index(
        "ix_video_jobs_created_at", "video_jobs", ["created_at"], unique=False
    )
    op.create_index(
        "ix_video_jobs_status_created_at",
        "video_jobs",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_video_jobs_status_created_at", table_name="video_jobs")
    op.drop_index("ix_video_jobs_created_at", table_name="video_jobs")
    op.drop_index("ix_video_jobs_status", table_name="video_jobs")
    op.drop_index("ix_video_jobs_audio_job_id", table_name="video_jobs")
    op.drop_table("video_jobs")
