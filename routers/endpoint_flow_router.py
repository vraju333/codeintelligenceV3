from fastapi import (
    APIRouter,
    HTTPException,
    Query
)

from services.python.flow.python_endpoint_flow_service import (
    PythonEndpointFlowService
)


from services.python.flow.python_sample_data_service import PythonSampleDataService

router = APIRouter(
    prefix="/api/endpoint-flow",
    tags=["Endpoint Flow"]
)


@router.get("/endpoints")
def get_endpoints():

    try:
        service = PythonEndpointFlowService()

        endpoints = (
            service.discover_endpoints()
        )

        return {
            "total": len(endpoints),
            "endpoints": endpoints
        }

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )


@router.get("/analyze")
def analyze_endpoint(
    http_method: str = Query(...),
    endpoint: str = Query(...)
):

    try:
        service = PythonEndpointFlowService()

        return service.analyze_endpoint(
            http_method=http_method,
            endpoint=endpoint
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=404,
            detail=str(exception)
        )

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )

@router.get("/sample-data")
def get_sample_data(http_method: str = Query(...), endpoint: str = Query(...)):
    try:
        return PythonSampleDataService().generate(http_method=http_method, endpoint=endpoint)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
