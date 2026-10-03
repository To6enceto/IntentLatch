import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import policies, teams
from ..auth import require_admin
from ..policies import PolicyCreate, PolicyUpdate
from ..teams import EmployeeCreate, TeamCreate, TeamUpdate

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

NO_STORE = {"Cache-Control": "no-store"}


def signing_key(request: Request) -> str:
    return request.app.state.settings.token_signing_key.get_secret_value()


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
