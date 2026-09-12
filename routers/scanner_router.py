from fastapi import APIRouter

from schemas import ScanResponse
from services.python.scanner.python_scanner_service import PythonScannerService


router = APIRouter(
    prefix="/api/code",
    tags=["Code Analysis"]
)

service = PythonScannerService()


@router.post("/scan", response_model=ScanResponse)
def scan_python_project():
    return service.scan()
