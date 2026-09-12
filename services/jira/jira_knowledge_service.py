from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy.orm import Session

from db_models import JiraKnowledge
from config import settings
from services.python.scanner.python_scanner_service import PythonScannerService


class JiraKnowledgeService:
    """Project-isolated local JIRA knowledge base.

    DB rows are filtered by the active project and every project gets its own
    FAISS index directory.  This prevents a Java/Python or project A/project B
    JIRA from being returned for the wrong project when a relational DB is
    shared by multiple CodeIntelligence engines.
    """

    def __init__(self):
        self.index_root = Path("jira_rag_indexes")
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.vector_store: FAISS | None = None
        self.loaded_project_path: str | None = None

    @staticmethod
    def _active_project_path() -> str:
        value = str(settings.PYTHON_PROJECT_PATH or "").strip()
        if not value:
            raise ValueError("No active Python project is selected")
        try:
            return str(Path(value).expanduser().resolve())
        except Exception:
            return value

    def _index_path(self, project_path: str) -> Path:
        # Human-readable folder plus a short path hash so identical project
        # folder names in different locations never share an index.
        name = Path(project_path).name or "project"
        digest = hashlib.sha256(project_path.lower().encode("utf-8")).hexdigest()[:12]
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self.index_root / f"{safe_name}-{digest}"

    def _ensure_loaded(self, project_path: str) -> None:
        if self.loaded_project_path == project_path and self.vector_store is not None:
            return

        self.vector_store = None
        self.loaded_project_path = project_path
        index_path = self._index_path(project_path)
        if not (index_path / "index.faiss").exists():
            return
        try:
            self.vector_store = FAISS.load_local(
                str(index_path),
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
        except Exception:
            self.vector_store = None

    def save_and_index(
        self,
        db: Session,
        jira_id: str,
        requirement: str,
        title: str | None = None,
    ) -> dict:
        jira_id = (jira_id or "").strip().upper()
        requirement = (requirement or "").strip()
        title = (title or "").strip() or None

        if not jira_id:
            raise ValueError("JIRA ID is required")
        if len(requirement) < 3:
            raise ValueError("Requirement is required")

        try:
            project_path = PythonScannerService().scan().project_path
        except Exception:
            project_path = self._active_project_path()

        # Project + JIRA ID is the logical identity. Never update a JIRA row
        # that belongs to another project just because its key matches.
        row = (
            db.query(JiraKnowledge)
            .filter(
                JiraKnowledge.jira_id == jira_id,
                JiraKnowledge.project_path == project_path,
            )
            .first()
        )
        if row:
            row.title = title
            row.requirement = requirement
            action = "updated"
        else:
            row = JiraKnowledge(
                jira_id=jira_id,
                title=title,
                requirement=requirement,
                project_path=project_path,
            )
            db.add(row)
            action = "created"

        db.commit()
        db.refresh(row)
        index_info = self.rebuild_index(db, project_path=project_path)

        return {
            "status": "SAVED_AND_INDEXED",
            "action": action,
            "jira": self._to_dict(row),
            "index": index_info,
        }

    def list_all(self, db: Session) -> list[dict]:
        project_path = self._active_project_path()
        rows = (
            db.query(JiraKnowledge)
            .filter(JiraKnowledge.project_path == project_path)
            .order_by(JiraKnowledge.updated_at.desc())
            .all()
        )
        return [self._to_dict(row) for row in rows]

    def rebuild_index(self, db: Session, project_path: str | None = None) -> dict:
        project_path = project_path or self._active_project_path()
        rows = (
            db.query(JiraKnowledge)
            .filter(JiraKnowledge.project_path == project_path)
            .order_by(JiraKnowledge.id)
            .all()
        )
        if not rows:
            self.vector_store = None
            self.loaded_project_path = project_path
            return {"documents": 0, "status": "EMPTY", "project_path": project_path}

        documents = []
        for row in rows:
            text = "\n".join(filter(None, [
                f"JIRA: {row.jira_id}",
                f"Title: {row.title}" if row.title else None,
                f"Requirement: {row.requirement}",
                f"Project: {row.project_path}" if row.project_path else None,
            ]))
            documents.append(Document(
                page_content=text,
                metadata={
                    "jira_id": row.jira_id,
                    "title": row.title or "",
                    "project_path": row.project_path or "",
                },
            ))

        self.vector_store = FAISS.from_documents(documents, self.embeddings)
        self.loaded_project_path = project_path
        index_path = self._index_path(project_path)
        index_path.mkdir(parents=True, exist_ok=True)
        self.vector_store.save_local(str(index_path))
        return {
            "documents": len(documents),
            "status": "INDEXED",
            "project_path": project_path,
            "index_path": str(index_path),
        }

    def search(self, db: Session, query: str, top_k: int = 5) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        project_path = self._active_project_path()
        self._ensure_loaded(project_path)
        if self.vector_store is None:
            self.rebuild_index(db, project_path=project_path)
        if self.vector_store is None:
            return []

        # Ask FAISS for extra candidates, then still enforce project identity
        # at both metadata and DB levels as defense in depth.
        matches = self.vector_store.similarity_search_with_score(query, k=max(top_k * 3, top_k))
        results = []
        for doc, score in matches:
            if str(doc.metadata.get("project_path") or "") != project_path:
                continue
            jira_id = doc.metadata.get("jira_id")
            row = (
                db.query(JiraKnowledge)
                .filter(
                    JiraKnowledge.jira_id == jira_id,
                    JiraKnowledge.project_path == project_path,
                )
                .first()
            )
            if not row:
                continue
            results.append({
                **self._to_dict(row),
                "similarity_score": float(score),
                "matched_by": "JIRA_RAG",
            })
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _to_dict(row: JiraKnowledge) -> dict:
        return {
            "id": row.id,
            "jira_id": row.jira_id,
            "title": row.title,
            "requirement": row.requirement,
            "project_path": row.project_path,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


jira_knowledge_service = JiraKnowledgeService()
