"""OpenAPI of the public testcase API (docs/specs/30 §15, G11).

/api/v1/docs is taken by the dự án documents (project_docs.py), so the API documentation lives
under the API itself: /api/v1/testcases/api-docs (Swagger UI) and .../openapi.json. Both need a
signed-in dashboard session or a personal access token (`cpat_`, testcases:read). The default
FastAPI /openapi.json and /docs are left as they are.
"""

import copy
from typing import Any

from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.security import TESTCASE_API, CurrentPrincipal

router = APIRouter(tags=["testcases"])

DOCS_PATH = "/testcases/api-docs"
BEARER = "cpatToken"
DESCRIPTION = (
    "API công khai của Testcase. Xác thực bằng token cá nhân (Của tôi > Token): "
    "`Authorization: Bearer cpat_...`. Scope: `testcases:read` (đọc), `testcases:write` (sửa), "
    "`results:write` (chỉ ghi kết quả: POST /testcases/tests/{id}/results, "
    "/testcases/runs/{id}/results, /testcases/runs/import-results). Token gắn một dự án chỉ "
    "thấy dự án đó. Giới hạn 600 lần gọi mỗi phút mỗi token (429)."
)


def testcase_openapi(full: dict[str, Any]) -> dict[str, Any]:
    """The app's OpenAPI narrowed to /api/v1/testcases/*, with the Bearer scheme documented."""
    spec = copy.deepcopy(full)
    spec["paths"] = {
        path: item
        for path, item in spec.get("paths", {}).items()
        if path == TESTCASE_API or path.startswith(TESTCASE_API + "/")
    }
    info = spec.setdefault("info", {})
    info["title"] = "CodePulse Testcase API"
    info["description"] = DESCRIPTION
    spec.setdefault("components", {}).setdefault("securitySchemes", {})[BEARER] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "cpat_<token>",
        "description": "Token cá nhân tạo ở trang Của tôi > Token.",
    }
    spec["security"] = [{BEARER: []}]
    return spec


@router.get(f"{DOCS_PATH}/openapi.json", include_in_schema=False)
def openapi_json(request: Request, principal: CurrentPrincipal) -> JSONResponse:
    return JSONResponse(testcase_openapi(request.app.openapi()))


@router.get(DOCS_PATH, include_in_schema=False)
def swagger_ui(request: Request, principal: CurrentPrincipal) -> HTMLResponse:
    base = request.scope.get("root_path", "")
    return get_swagger_ui_html(
        openapi_url=f"{base}/api/v1{DOCS_PATH}/openapi.json",
        title="CodePulse Testcase API",
    )
