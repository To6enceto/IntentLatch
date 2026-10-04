import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from .. import policies, reports, runner, teams, testcases
from ..auth import require_admin
from ..policies import PolicyCreate, PolicyUpdate
from ..runner import RunRequest
from ..teams import EmployeeCreate, TeamCreate, TeamUpdate
from ..testcases import CaseCreate, CaseUpdate

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

NO_STORE = {"Cache-Control": "no-store"}

From = Annotated[AwareDatetime, Query(alias="from")]
To = Annotated[AwareDatetime, Query(alias="to")]


def signing_key(request: Request) -> str:
    return request.app.state.settings.token_signing_key.get_secret_value()


async def report_items(
    request: Request, start: datetime, end: datetime
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    start, end = reports.report_range(start, end)
    return start, end, await reports.load_items(request.app.state.pool, start, end)


def csv_download(text: str, name: str) -> Response:
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', **NO_STORE},
    )


@router.post("/teams", status_code=201)
async def create_team(body: TeamCreate, request: Request) -> dict[str, Any]:
    return await teams.create_team(request.app.state.pool, body)


@router.get("/teams")
async def list_teams(request: Request) -> dict[str, Any]:
    return {"teams": await teams.list_teams(request.app.state.pool)}


@router.patch("/teams/{team_id}")
async def update_team(team_id: uuid.UUID, body: TeamUpdate, request: Request) -> dict[str, Any]:
    return await teams.update_team(request.app.state.pool, team_id, body)


@router.post("/teams/{team_id}/employees")
async def create_employee(team_id: uuid.UUID, body: EmployeeCreate, request: Request) -> JSONResponse:
    employee, token = await teams.create_employee(
        request.app.state.pool, team_id, body, signing_key(request)
    )
    return JSONResponse({"employee": employee, "token": token}, status_code=201, headers=NO_STORE)


@router.get("/teams/{team_id}/employees")
async def list_employees(team_id: uuid.UUID, request: Request) -> dict[str, Any]:
    return {"employees": await teams.list_employees(request.app.state.pool, team_id)}


@router.post("/employees/{employee_id}/token")
async def reissue_token(employee_id: uuid.UUID, request: Request) -> JSONResponse:
    employee, token = await teams.reissue_token(
        request.app.state.pool, employee_id, signing_key(request)
    )
    return JSONResponse({"employee": employee, "token": token}, headers=NO_STORE)


@router.post("/employees/{employee_id}/revoke")
async def revoke(employee_id: uuid.UUID, request: Request) -> dict[str, Any]:
    return await teams.revoke(request.app.state.pool, employee_id)


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyCreate, request: Request) -> dict[str, Any]:
    return await policies.create_policy(request.app.state.pool, body)


@router.get("/policies")
async def list_policies(request: Request) -> dict[str, Any]:
    return await policies.list_policies(request.app.state.pool)


@router.patch("/policies/{code}")
async def update_policy(code: str, body: PolicyUpdate, request: Request) -> dict[str, Any]:
    return await policies.update_policy(request.app.state.pool, code, body)


@router.post("/test-cases", status_code=201)
async def create_test_case(body: CaseCreate, request: Request) -> dict[str, Any]:
    return {"test_case": await testcases.create_case(request.app.state.pool, body)}


@router.get("/test-cases")
async def list_test_cases(request: Request) -> dict[str, Any]:
    return {"test_cases": await testcases.list_cases(request.app.state.pool)}


@router.patch("/test-cases/{code}")
async def update_test_case(code: str, body: CaseUpdate, request: Request) -> dict[str, Any]:
    return {"test_case": await testcases.update_case(request.app.state.pool, code, body)}


@router.post("/test-runs", status_code=201)
async def start_test_run(request: Request, body: RunRequest | None = None) -> dict[str, Any]:
    # Answers when the run ends; every result is stored as soon as its case finishes.
    body = body or RunRequest()
    cases = await testcases.load_cases(request.app.state.pool, body.cases)
    return {"test_run": await runner.execute(request.app, cases, body.started_by)}


@router.get("/test-runs")
async def list_test_runs(request: Request) -> dict[str, Any]:
    return {"test_runs": await runner.list_runs(request.app.state.pool)}


@router.get("/test-runs/{run_id}")
async def get_test_run(run_id: uuid.UUID, request: Request) -> dict[str, Any]:
    return {"test_run": await runner.get_run(request.app.state.pool, run_id)}


@router.get("/reports")
async def report(request: Request, start: From, end: To) -> JSONResponse:
    start, end, items = await report_items(request, start, end)
    return JSONResponse(reports.build_report(start, end, items), headers=NO_STORE)


@router.get("/reports/items.csv")
async def report_items_csv(request: Request, start: From, end: To) -> Response:
    start, end, items = await report_items(request, start, end)
    return csv_download(reports.items_csv(items), reports.filename(start, end, "items"))


@router.get("/reports/totals.csv")
async def report_totals_csv(request: Request, start: From, end: To) -> Response:
    start, end, items = await report_items(request, start, end)
    return csv_download(reports.totals_csv(reports.totals(items)), reports.filename(start, end, "totals"))
