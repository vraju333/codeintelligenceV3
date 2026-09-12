from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from database import Base


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_path", "scenario_code", name="uq_scenario_project_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scenario_code = Column(String(100), nullable=False, index=True)
    scenario_name = Column(String(200), nullable=False)
    http_method = Column(String(20), nullable=False)
    endpoint = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    jira_id = Column(String(100), nullable=True, index=True)
    request_json = Column(Text, nullable=True)
    expected_response_json = Column(Text, nullable=True)
    expected_db_effect = Column(Text, nullable=True)
    involved_classes = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="ACTIVE")
    project_path = Column(Text, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class JiraKnowledge(Base):
    """Locally saved JIRA/requirement document used by the demo JIRA RAG index."""
    __tablename__ = "jira_knowledge"
    __table_args__ = (
        UniqueConstraint("project_path", "jira_id", name="uq_jira_project_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    jira_id = Column(String(100), nullable=False, index=True)
    title = Column(String(300), nullable=True)
    requirement = Column(Text, nullable=False)
    project_path = Column(Text, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
