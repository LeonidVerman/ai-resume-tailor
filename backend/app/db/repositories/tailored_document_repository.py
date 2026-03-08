"""
backend/app/db/repositories/tailored_document_repository.py

CRUD operations for TailoredDocument.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.tailored_document import TailoredDocument


class TailoredDocumentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, doc_id: str) -> TailoredDocument | None:
        return self._db.get(TailoredDocument, doc_id)

    def list_by_user_id(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[TailoredDocument]:
        return (
            self._db.query(TailoredDocument)
            .filter(TailoredDocument.user_id == user_id)
            .order_by(TailoredDocument.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def get_by_generation_run_id(self, run_id: str) -> TailoredDocument | None:
        return (
            self._db.query(TailoredDocument)
            .filter(TailoredDocument.generation_run_id == run_id)
            .first()
        )

    def create(self, **kwargs) -> TailoredDocument:
        doc = TailoredDocument(**kwargs)
        self._db.add(doc)
        self._db.flush()
        return doc

    def update(self, doc: TailoredDocument, **kwargs) -> TailoredDocument:
        for key, value in kwargs.items():
            setattr(doc, key, value)
        self._db.flush()
        return doc

    def delete(self, doc: TailoredDocument) -> None:
        self._db.delete(doc)
        self._db.flush()
