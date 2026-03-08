"""
backend/app/db/repositories/user_repository.py

CRUD operations for the User model.
Business logic belongs in services, not here.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.user import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, user_id: str) -> User | None:
        return self._db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self._db.query(User).filter(User.email == email).first()

    def create(self, **kwargs) -> User:
        user = User(**kwargs)
        self._db.add(user)
        self._db.flush()
        return user

    def update(self, user: User, **kwargs) -> User:
        for key, value in kwargs.items():
            setattr(user, key, value)
        self._db.flush()
        return user

    def delete(self, user: User) -> None:
        self._db.delete(user)
        self._db.flush()

    def list_all(self, limit: int = 100, offset: int = 0) -> list[User]:
        return self._db.query(User).limit(limit).offset(offset).all()
