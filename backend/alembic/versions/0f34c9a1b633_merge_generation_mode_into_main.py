"""merge_generation_mode_into_main

Revision ID: 0f34c9a1b633
Revises: 410489c81d63, a9b0c1d2e3f4
Create Date: 2026-04-07 09:33:51.206389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f34c9a1b633'
down_revision: Union[str, None] = ('410489c81d63', 'a9b0c1d2e3f4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
