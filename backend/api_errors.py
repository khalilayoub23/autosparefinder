"""
Script: api_errors.py
Purpose: Request-validation (422) responses that never echo the submitted values (FIXES_TRACKER #164).
Process: FastAPI's default RequestValidationError handler returns every rejected value ("input", and "ctx" for
  custom validators): a too-short new password, a reset token or a 2FA code came back verbatim in the response.
  validation_exception_handler keeps the default shape ({"detail": [{type, loc, msg, url}]}, status 422) and drops
  those two keys. Registered for the whole app in BACKEND_API_ROUTES.py.
Data Imported/Modified: none.
Data Sources: none.
Missing Data Delegation: none.
Last Updated: 2026-11-28
"""
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_ECHO_KEYS = ("input", "ctx")


def redacted_validation_errors(exc: RequestValidationError) -> list:
    return jsonable_encoder([{k: v for k, v in err.items() if k not in _ECHO_KEYS} for err in exc.errors()])


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": redacted_validation_errors(exc)})
