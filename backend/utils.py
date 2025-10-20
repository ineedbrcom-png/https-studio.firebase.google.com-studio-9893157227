"""Utility helpers for request parsing and validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Optional

from .errors import HttpError


@dataclass
class RequestContext:
    method: str
    path: str
    path_params: Dict[str, str]
    query: Dict[str, Any]
    headers: Dict[str, str]
    body: bytes
    user: Optional[Dict[str, Any]] = None
    _json_cache: Optional[Dict[str, Any]] = None

    def json(self) -> Dict[str, Any]:
        if self._json_cache is not None:
            return self._json_cache
        if not self.body:
            self._json_cache = {}
            return self._json_cache
        try:
            data = json.loads(self.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "Invalid JSON payload") from exc
        if not isinstance(data, dict):
            raise HttpError.bad_request("JSON payload must be an object")
        self._json_cache = data
        return data


class ResponseBuilder:
    def __init__(self, handler) -> None:  # handler: BaseHTTPRequestHandler
        self._handler = handler

    def send_json(self, payload: Any, *, status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self._handler.send_response(status)
        self._handler.send_header("Content-Type", "application/json; charset=utf-8")
        self._handler.send_header("Content-Length", str(len(encoded)))
        self._handler.send_header("Access-Control-Allow-Origin", "*")
        self._handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self._handler.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self._handler.end_headers()
        self._handler.wfile.write(encoded)

    def send_no_content(self) -> None:
        self._handler.send_response(HTTPStatus.NO_CONTENT)
        self._handler.send_header("Access-Control-Allow-Origin", "*")
        self._handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self._handler.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self._handler.end_headers()

