"""
backend/app/db/repositories/billing_repository.py

CRUD operations for Billing.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.billing import Billing


class BillingRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_user_id(self, user_id: str) -> Billing | None:
        return (
            self._db.query(Billing)
            .filter(Billing.user_id == user_id)
            .first()
        )

    def get_by_stripe_customer_id(self, stripe_customer_id: str) -> Billing | None:
        return (
            self._db.query(Billing)
            .filter(Billing.stripe_customer_id == stripe_customer_id)
            .first()
        )

    def create(self, **kwargs) -> Billing:
        billing = Billing(**kwargs)
        self._db.add(billing)
        self._db.flush()
        return billing

    def update(self, billing: Billing, **kwargs) -> Billing:
        for key, value in kwargs.items():
            setattr(billing, key, value)
        self._db.flush()
        return billing
