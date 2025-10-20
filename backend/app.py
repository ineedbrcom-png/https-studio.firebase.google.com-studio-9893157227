"""HTTP entry-point for the Studio backend."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import database
from .auth import create_token, decode_token, hash_password, verify_password
from .errors import HttpError, ValidationError
from .router import Router
from .utils import RequestContext, ResponseBuilder

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _row_to_dict(row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _log_activity(conn, project_id: int, user_id: Optional[int], action: str, details: str) -> None:
    conn.execute(
        "INSERT INTO activity_logs (project_id, user_id, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, user_id, action, details, _now()),
    )


def _fetch_user(conn, user_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, email, name, role, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _require_project_access(conn, project_id: int, user_id: int) -> Dict[str, Any]:
    project_row = conn.execute(
        """
        SELECT p.*, CASE WHEN p.owner_id = ? THEN 1 ELSE 0 END AS is_owner
        FROM projects p
        LEFT JOIN project_members pm ON pm.project_id = p.id AND pm.user_id = ?
        WHERE p.id = ? AND (p.owner_id = ? OR pm.user_id IS NOT NULL)
        """,
        (user_id, user_id, project_id, user_id),
    ).fetchone()
    if not project_row:
        raise HttpError.forbidden("You do not have access to this project")
    return _row_to_dict(project_row)


def _serialize_project(conn, project_row: Dict[str, Any]) -> Dict[str, Any]:
    project_id = project_row["id"]
    members = [
        {
            "id": row["user_id"],
            "name": row["name"],
            "email": row["email"],
            "role": row["role"],
            "joined_at": row["created_at"],
        }
        for row in conn.execute(
            """
            SELECT pm.user_id, u.name, u.email, pm.role, pm.created_at
            FROM project_members pm
            JOIN users u ON u.id = pm.user_id
            WHERE pm.project_id = ?
            ORDER BY u.name
            """,
            (project_id,),
        )
    ]

    tasks = [
        {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "due_date": row["due_date"],
            "assignee_id": row["assignee_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in conn.execute(
            """
            SELECT * FROM tasks
            WHERE project_id = ?
            ORDER BY created_at DESC
            """,
            (project_id,),
        )
    ]

    assets = [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "url": row["url"],
            "created_at": row["created_at"],
        }
        for row in conn.execute(
            "SELECT * FROM assets WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
    ]

    payload = {
        "id": project_row["id"],
        "name": project_row["name"],
        "description": project_row["description"],
        "status": project_row["status"],
        "owner_id": project_row["owner_id"],
        "created_at": project_row["created_at"],
        "updated_at": project_row["updated_at"],
        "members": members,
        "tasks": tasks,
        "assets": assets,
    }
    return payload


router = Router()


def route(method: str, pattern: str) -> Callable:
    def decorator(func: Callable) -> Callable:
        router.add(method, pattern, func)
        return func

    return decorator


def require_auth(func: Callable) -> Callable:
    def wrapper(request: RequestContext, response: ResponseBuilder) -> None:
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise HttpError.unauthorized("Missing bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise HttpError.unauthorized("Invalid token payload")
        with database.get_connection() as conn:
            user = _fetch_user(conn, int(user_id))
        if not user:
            raise HttpError.unauthorized("User not found")
        request.user = user
        return func(request, response)

    return wrapper


@route("GET", "/")
def handle_root(request: RequestContext, response: ResponseBuilder) -> None:
    response.send_json({"name": "Studio Backend", "status": "ok"})


@route("GET", "/health")
def handle_health(request: RequestContext, response: ResponseBuilder) -> None:
    response.send_json({"status": "healthy"})


@route("POST", "/api/auth/register")
def handle_register(request: RequestContext, response: ResponseBuilder) -> None:
    payload = request.json()
    email = _normalize_email(payload.get("email", ""))
    name = payload.get("name", "").strip()
    password = payload.get("password", "")

    if not email or "@" not in email:
        raise ValidationError("Um e-mail válido é obrigatório.")
    if len(name) < 2:
        raise ValidationError("O nome deve ter pelo menos 2 caracteres.")
    if len(password) < 8:
        raise ValidationError("A senha deve ter pelo menos 8 caracteres.")

    password_data = hash_password(password)

    with database.get_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValidationError("Este e-mail já está cadastrado.")
        cursor = conn.execute(
            "INSERT INTO users (email, name, password_hash, salt, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                email,
                name,
                password_data["hash"],
                password_data["salt"],
                payload.get("role", "member"),
                _now(),
            ),
        )
        user_id = cursor.lastrowid
        user = _fetch_user(conn, user_id)

    token = create_token({"sub": user_id, "email": email})
    response.send_json({"token": token, "user": user}, status=HTTPStatus.CREATED)


@route("POST", "/api/auth/login")
def handle_login(request: RequestContext, response: ResponseBuilder) -> None:
    payload = request.json()
    email = _normalize_email(payload.get("email", ""))
    password = payload.get("password", "")

    if not email or not password:
        raise ValidationError("E-mail e senha são obrigatórios.")

    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row:
            raise HttpError.unauthorized("Credenciais inválidas.")
        if not verify_password(password, row["password_hash"], row["salt"]):
            raise HttpError.unauthorized("Credenciais inválidas.")
        user = {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    token = create_token({"sub": row["id"], "email": row["email"]})
    response.send_json({"token": token, "user": user})


@route("GET", "/api/projects")
@require_auth
def handle_list_projects(request: RequestContext, response: ResponseBuilder) -> None:
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        projects = [
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "description": row["description"],
                "owner_id": row["owner_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_owner": row["owner_id"] == user_id,
            }
            for row in conn.execute(
                """
                SELECT DISTINCT p.*
                FROM projects p
                LEFT JOIN project_members pm ON pm.project_id = p.id
                WHERE p.owner_id = ? OR pm.user_id = ?
                ORDER BY p.updated_at DESC
                """,
                (user_id, user_id),
            )
        ]
    response.send_json({"projects": projects})


@route("POST", "/api/projects")
@require_auth
def handle_create_project(request: RequestContext, response: ResponseBuilder) -> None:
    payload = request.json()
    name = payload.get("name", "").strip()
    description = payload.get("description", "").strip() or None
    status = payload.get("status", "draft")

    if len(name) < 3:
        raise ValidationError("O nome do projeto deve ter pelo menos 3 caracteres.")
    if status not in {"draft", "active", "completed"}:
        raise ValidationError("Status inválido. Use draft, active ou completed.")

    user_id = int(request.user["id"])
    now = _now()
    with database.get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO projects (name, description, status, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, status, user_id, now, now),
        )
        project_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
            (project_id, user_id, "owner", now),
        )
        _log_activity(conn, project_id, user_id, "project_created", f"Projeto '{name}' criado.")
        project_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        payload = _serialize_project(conn, _row_to_dict(project_row))
    response.send_json(payload, status=HTTPStatus.CREATED)


@route("GET", "/api/projects/:project_id")
@require_auth
def handle_get_project(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        project_row = _require_project_access(conn, project_id, user_id)
        payload = _serialize_project(conn, project_row)
    response.send_json(payload)


@route("PUT", "/api/projects/:project_id")
@require_auth
def handle_update_project(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    payload = request.json()
    name = payload.get("name")
    description = payload.get("description")
    status = payload.get("status")

    allowed_status = {"draft", "active", "completed"}
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        project_row = _require_project_access(conn, project_id, user_id)
        if project_row["owner_id"] != user_id:
            raise HttpError.forbidden("Apenas o proprietário pode atualizar o projeto.")
        fields = []
        params = []
        if name:
            if len(name.strip()) < 3:
                raise ValidationError("O nome do projeto deve ter pelo menos 3 caracteres.")
            fields.append("name = ?")
            params.append(name.strip())
        if description is not None:
            fields.append("description = ?")
            params.append(description.strip())
        if status:
            if status not in allowed_status:
                raise ValidationError("Status inválido. Use draft, active ou completed.")
            fields.append("status = ?")
            params.append(status)
        if not fields:
            raise ValidationError("Nenhuma alteração fornecida.")
        fields.append("updated_at = ?")
        params.append(_now())
        params.append(project_id)
        conn.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", params)
        _log_activity(conn, project_id, user_id, "project_updated", json.dumps(payload))
        project_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        result = _serialize_project(conn, _row_to_dict(project_row))
    response.send_json(result)


@route("DELETE", "/api/projects/:project_id")
@require_auth
def handle_delete_project(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        project_row = _require_project_access(conn, project_id, user_id)
        if project_row["owner_id"] != user_id:
            raise HttpError.forbidden("Apenas o proprietário pode excluir o projeto.")
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        _log_activity(conn, project_id, user_id, "project_deleted", "Projeto excluído")
    response.send_no_content()


@route("POST", "/api/projects/:project_id/tasks")
@require_auth
def handle_create_task(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    payload = request.json()
    title = payload.get("title", "").strip()
    description = payload.get("description")
    status = payload.get("status", "pending")
    due_date = payload.get("due_date")
    assignee_id = payload.get("assignee_id")

    if len(title) < 3:
        raise ValidationError("O título da tarefa deve ter pelo menos 3 caracteres.")
    if status not in {"pending", "in_progress", "completed"}:
        raise ValidationError("Status de tarefa inválido.")

    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        _require_project_access(conn, project_id, user_id)
        if assignee_id is not None:
            member = conn.execute(
                "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, assignee_id),
            ).fetchone()
            if not member:
                raise ValidationError("O responsável precisa fazer parte do projeto.")
        cursor = conn.execute(
            """
            INSERT INTO tasks (project_id, title, description, status, due_date, assignee_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                title,
                (description or "").strip() or None,
                status,
                due_date,
                assignee_id,
                _now(),
                _now(),
            ),
        )
        task_id = cursor.lastrowid
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        _log_activity(conn, project_id, user_id, "task_created", json.dumps({"task_id": task_id, "title": title}))
    response.send_json(_row_to_dict(task_row), status=HTTPStatus.CREATED)


@route("GET", "/api/projects/:project_id/tasks")
@require_auth
def handle_list_tasks(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        _require_project_access(conn, project_id, user_id)
        tasks = [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "due_date": row["due_date"],
                "assignee_id": row["assignee_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
        ]
    response.send_json({"tasks": tasks})


@route("PATCH", "/api/tasks/:task_id")
@require_auth
def handle_update_task(request: RequestContext, response: ResponseBuilder) -> None:
    task_id = int(request.path_params["task_id"])
    payload = request.json()
    allowed_status = {"pending", "in_progress", "completed"}
    fields = []
    params: list[Any] = []

    with database.get_connection() as conn:
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task_row:
            raise HttpError.not_found("Tarefa não encontrada.")
        project_id = task_row["project_id"]
        _require_project_access(conn, project_id, int(request.user["id"]))

        if "title" in payload:
            title = payload["title"].strip()
            if len(title) < 3:
                raise ValidationError("O título da tarefa deve ter pelo menos 3 caracteres.")
            fields.append("title = ?")
            params.append(title)
        if "description" in payload:
            description = (payload.get("description") or "").strip() or None
            fields.append("description = ?")
            params.append(description)
        if "status" in payload:
            status = payload.get("status")
            if status not in allowed_status:
                raise ValidationError("Status de tarefa inválido.")
            fields.append("status = ?")
            params.append(status)
        if "due_date" in payload:
            fields.append("due_date = ?")
            params.append(payload.get("due_date"))
        if "assignee_id" in payload:
            assignee_id = payload.get("assignee_id")
            if assignee_id is not None:
                member = conn.execute(
                    "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                    (project_id, assignee_id),
                ).fetchone()
                if not member:
                    raise ValidationError("O responsável precisa fazer parte do projeto.")
            fields.append("assignee_id = ?")
            params.append(assignee_id)
        if not fields:
            raise ValidationError("Nenhuma alteração fornecida.")
        fields.append("updated_at = ?")
        params.append(_now())
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", params)
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        _log_activity(conn, project_id, int(request.user["id"]), "task_updated", json.dumps(payload))
    response.send_json(_row_to_dict(task_row))


@route("DELETE", "/api/tasks/:task_id")
@require_auth
def handle_delete_task(request: RequestContext, response: ResponseBuilder) -> None:
    task_id = int(request.path_params["task_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task_row:
            raise HttpError.not_found("Tarefa não encontrada.")
        project_id = task_row["project_id"]
        _require_project_access(conn, project_id, user_id)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        _log_activity(
            conn,
            project_id,
            user_id,
            "task_deleted",
            json.dumps({"task_id": task_id}),
        )
    response.send_no_content()


@route("POST", "/api/projects/:project_id/assets")
@require_auth
def handle_create_asset(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    payload = request.json()
    name = payload.get("name", "").strip()
    asset_type = payload.get("type", "").strip()
    url = payload.get("url", "").strip()

    if not name:
        raise ValidationError("O nome do arquivo é obrigatório.")
    if not asset_type:
        raise ValidationError("O tipo do arquivo é obrigatório.")
    if not url:
        raise ValidationError("A URL do arquivo é obrigatória.")

    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        _require_project_access(conn, project_id, user_id)
        cursor = conn.execute(
            "INSERT INTO assets (project_id, name, url, type, created_at) VALUES (?, ?, ?, ?, ?)",
            (project_id, name, url, asset_type, _now()),
        )
        asset_id = cursor.lastrowid
        asset_row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        _log_activity(conn, project_id, user_id, "asset_uploaded", json.dumps({"asset_id": asset_id, "name": name}))
    response.send_json(_row_to_dict(asset_row), status=HTTPStatus.CREATED)


@route("GET", "/api/projects/:project_id/assets")
@require_auth
def handle_list_assets(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        _require_project_access(conn, project_id, user_id)
        assets = [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "url": row["url"],
                "created_at": row["created_at"],
            }
            for row in conn.execute(
                "SELECT * FROM assets WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
        ]
    response.send_json({"assets": assets})


@route("DELETE", "/api/assets/:asset_id")
@require_auth
def handle_delete_asset(request: RequestContext, response: ResponseBuilder) -> None:
    asset_id = int(request.path_params["asset_id"])
    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        asset_row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not asset_row:
            raise HttpError.not_found("Arquivo não encontrado.")
        project_id = asset_row["project_id"]
        _require_project_access(conn, project_id, user_id)
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        _log_activity(
            conn,
            project_id,
            user_id,
            "asset_deleted",
            json.dumps({"asset_id": asset_id}),
        )
    response.send_no_content()


@route("POST", "/api/projects/:project_id/members")
@require_auth
def handle_add_member(request: RequestContext, response: ResponseBuilder) -> None:
    project_id = int(request.path_params["project_id"])
    payload = request.json()
    email = _normalize_email(payload.get("email", ""))
    role = payload.get("role", "collaborator")

    if not email:
        raise ValidationError("O e-mail do membro é obrigatório.")
    if role not in {"collaborator", "reviewer", "owner"}:
        raise ValidationError("Role inválida.")

    user_id = int(request.user["id"])
    with database.get_connection() as conn:
        project_row = _require_project_access(conn, project_id, user_id)
        if project_row["owner_id"] != user_id:
            raise HttpError.forbidden("Apenas o proprietário pode adicionar membros.")
        member_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not member_row:
            raise ValidationError("Usuário não encontrado.")
        member_id = member_row["id"]
        conn.execute(
            "INSERT OR IGNORE INTO project_members (project_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
            (project_id, member_id, role, _now()),
        )
        _log_activity(
            conn,
            project_id,
            user_id,
            "member_added",
            json.dumps({"member_id": member_id, "role": role}),
        )
        project_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        payload = _serialize_project(conn, _row_to_dict(project_row))
    response.send_json(payload)


@route("GET", "/api/activity")
@require_auth
def handle_activity_feed(request: RequestContext, response: ResponseBuilder) -> None:
    user_id = int(request.user["id"])
    limit = int(request.query.get("limit", 25))
    limit = max(1, min(limit, 100))

    with database.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT al.id, al.project_id, al.user_id, al.action, al.details, al.created_at,
                   p.name AS project_name,
                   u.name AS user_name
            FROM activity_logs al
            JOIN projects p ON p.id = al.project_id
            LEFT JOIN users u ON u.id = al.user_id
            WHERE al.project_id IN (
                SELECT project_id FROM project_members WHERE user_id = ?
                UNION
                SELECT id FROM projects WHERE owner_id = ?
            )
            ORDER BY al.created_at DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        ).fetchall()
    feed = [
        {
            "id": row["id"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "action": row["action"],
            "details": row["details"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    response.send_json({"activity": feed})


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class StudioRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        handler, params = router.match(method, path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        query_raw = parse_qs(parsed.query)
        query = {key: values[0] if len(values) == 1 else values for key, values in query_raw.items()}
        headers = {key.lower(): value for key, value in self.headers.items()}
        request = RequestContext(method, path, params, query, headers, body)
        response = ResponseBuilder(self)
        try:
            if not handler:
                raise HttpError.not_found()
            handler(request, response)
        except HttpError as exc:
            response.send_json({"error": exc.message, "details": exc.details}, status=exc.status)
        except Exception:  # pragma: no cover - defensive logging
            logging.exception("Unhandled server error")
            response.send_json({"error": "Erro interno"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_OPTIONS(self) -> None:  # noqa: N802 (method name imposed by BaseHTTPRequestHandler)
        allowed = router.allowed_methods(urlparse(self.path).path)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        methods = ",".join(sorted(set(allowed + ["OPTIONS"]))) if allowed else "GET,POST,PUT,PATCH,DELETE,OPTIONS"
        self.send_header("Access-Control-Allow-Methods", methods)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logging.info("%s - %s", self.client_address[0], format % args)


def create_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadedHTTPServer:
    database.initialize()
    server = ThreadedHTTPServer((host, port), StudioRequestHandler)
    return server


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = create_server(host, port)
    logging.info("Servidor iniciado em http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Encerrando servidor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()

