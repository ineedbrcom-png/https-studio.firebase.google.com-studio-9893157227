"""Simple router implementation used by the HTTP handler."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

Handler = Callable[[Any], Any]


@dataclass
class Route:
    method: str
    parts: List[str]
    handler: Callable


class Router:
    def __init__(self) -> None:
        self._routes: List[Route] = []

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        parts = [part for part in pattern.strip("/").split("/") if part]
        self._routes.append(Route(method.upper(), parts, handler))

    def match(self, method: str, path: str) -> Tuple[Optional[Callable], Dict[str, str]]:
        method = method.upper()
        path_parts = [part for part in path.strip("/").split("/") if part]
        for route in self._routes:
            if route.method != method:
                continue
            if len(route.parts) != len(path_parts):
                continue
            params: Dict[str, str] = {}
            matched = True
            for route_part, path_part in zip(route.parts, path_parts):
                if route_part.startswith(":"):
                    params[route_part[1:]] = path_part
                    continue
                if route_part != path_part:
                    matched = False
                    break
            if matched:
                return route.handler, params
        return None, {}

    def allowed_methods(self, path: str) -> List[str]:
        path_parts = [part for part in path.strip("/").split("/") if part]
        methods = set()
        for route in self._routes:
            if len(route.parts) != len(path_parts):
                continue
            match = True
            for route_part, path_part in zip(route.parts, path_parts):
                if route_part.startswith(":"):
                    continue
                if route_part != path_part:
                    match = False
                    break
            if match:
                methods.add(route.method)
        return sorted(methods)

