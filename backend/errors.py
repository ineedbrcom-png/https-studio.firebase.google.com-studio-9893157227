"""Custom exceptions used throughout the backend."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict, Optional


class HttpError(Exception):
    """Exception representing an HTTP error response."""

    def __init__(self, status: int, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}

    @classmethod
    def bad_request(cls, message: str, *, details: Optional[Dict[str, Any]] = None) -> "HttpError":
        return cls(HTTPStatus.BAD_REQUEST, message, details=details)

    @classmethod
    def unauthorized(cls, message: str = "Unauthorized") -> "HttpError":
        return cls(HTTPStatus.UNAUTHORIZED, message)

    @classmethod
    def forbidden(cls, message: str = "Forbidden") -> "HttpError":
        return cls(HTTPStatus.FORBIDDEN, message)

    @classmethod
    def not_found(cls, message: str = "Not found") -> "HttpError":
        return cls(HTTPStatus.NOT_FOUND, message)


class ValidationError(HttpError):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(HTTPStatus.UNPROCESSABLE_ENTITY, message, details=details)
