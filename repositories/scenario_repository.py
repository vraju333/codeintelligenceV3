from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from db_models import Scenario
from schemas import ScenarioRequest, ScenarioUpdateRequest


class ScenarioRepository:

    @staticmethod
    def _project_filter(query, project_path: str | None):
        # In a multi-project/shared DB setup we never fall back to rows from
        # another project.  A project path is the isolation boundary.
        if not project_path:
            return query.filter(Scenario.project_path.is_(None))
        return query.filter(Scenario.project_path == project_path)

    def find_all(self, db: Session, project_path: str | None = None):
        query = self._project_filter(db.query(Scenario), project_path)
        return query.order_by(Scenario.id).all()

    def find_all_for_project_or_legacy(self, db: Session, project_path: str | None = None):
        query = db.query(Scenario)
        if project_path:
            query = query.filter(or_(Scenario.project_path == project_path, Scenario.project_path.is_(None)))
        else:
            query = query.filter(Scenario.project_path.is_(None))
        return query.order_by(Scenario.id).all()

    def find_page_for_endpoints(
        self,
        db: Session,
        endpoints: list[dict],
        page: int,
        page_size: int,
        project_path: str | None = None,
    ):
        if not endpoints:
            return [], 0

        filters = []
        for item in endpoints:
            method = str(item.get("http_method", "")).upper()
            endpoint = str(item.get("endpoint", ""))
            if method and endpoint:
                filters.append(
                    and_(
                        Scenario.http_method == method,
                        Scenario.endpoint == endpoint,
                    )
                )

        if not filters:
            return [], 0

        query = db.query(Scenario).filter(or_(*filters))
        query = self._project_filter(query, project_path)
        total = query.count()
        items = (
            query.order_by(Scenario.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def find_all_for_endpoints(
        self,
        db: Session,
        endpoints: list[dict],
        project_path: str | None = None,
    ):
        if not endpoints:
            return []
        filters = []
        for item in endpoints:
            method = str(item.get("http_method", "")).upper()
            endpoint = str(item.get("endpoint", ""))
            if method and endpoint:
                filters.append(
                    and_(
                        Scenario.http_method == method,
                        Scenario.endpoint == endpoint,
                    )
                )
        if not filters:
            return []
        query = db.query(Scenario).filter(or_(*filters))
        query = self._project_filter(query, project_path)
        return query.order_by(Scenario.id).all()

    def find_by_id(self, db: Session, scenario_id: int, project_path: str | None = None):
        query = db.query(Scenario).filter(Scenario.id == scenario_id)
        query = self._project_filter(query, project_path)
        return query.first()

    def find_by_code(self, db: Session, scenario_code: str, project_path: str | None = None):
        query = db.query(Scenario).filter(Scenario.scenario_code == scenario_code)
        query = self._project_filter(query, project_path)
        return query.first()

    def find_by_operation(
        self,
        db: Session,
        http_method: str,
        endpoint: str,
        project_path: str | None = None,
    ):
        query = db.query(Scenario).filter(
            Scenario.http_method == str(http_method).upper(),
            Scenario.endpoint == endpoint,
        )
        query = self._project_filter(query, project_path)
        return query.order_by(Scenario.id).all()

    def create(self, db: Session, request: ScenarioRequest, project_path: str | None = None):
        data = request.model_dump()
        data["project_path"] = project_path
        scenario = Scenario(**data)
        db.add(scenario)
        db.commit()
        db.refresh(scenario)
        return scenario

    def update(self, db: Session, scenario: Scenario, request: ScenarioUpdateRequest):
        data = request.model_dump(exclude_unset=True)
        for field, value in data.items():
            if hasattr(scenario, field):
                setattr(scenario, field, value)
        db.commit()
        db.refresh(scenario)
        return scenario

    def delete(self, db: Session, scenario: Scenario):
        db.delete(scenario)
        db.commit()
