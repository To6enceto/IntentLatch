from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


class GatewayError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def describe_validation_error(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    first = errors[0]
    if first.get("type") == "json_invalid":
        return "Request body is not valid JSON."
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    return f"{location}: {first['msg']}" if location else first["msg"]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(400, "invalid_request", describe_validation_error(exc))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))
