"""merge legal branch into main

Revision ID: 410489c81d63
Revises: b7c8d9e0f1a2, b0c1d2e3f4a5
Create Date: 2026-03-28 17:44:54.460537

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '410489c81d63'
down_revision: Union[str, None] = ('b7c8d9e0f1a2', 'b0c1d2e3f4a5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
