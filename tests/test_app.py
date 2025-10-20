from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http import HTTPStatus
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")
test_db_path = Path(tempfile.gettempdir()) / f"studio_backend_test_{os.getpid()}.db"
if test_db_path.exists():
    test_db_path.unlink()
os.environ["DATABASE_PATH"] = str(test_db_path)

from backend import database  # noqa: E402  # isort:skip
from backend.app import create_server  # noqa: E402  # isort:skip


def _request(method: str, path: str, body: dict | None = None, token: str | None = None, *, port: int) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    connection.close()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"raw": raw}
    return response.status, data


class StudioBackendIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        database.initialize()
        server = create_server("127.0.0.1", 0)
        cls._server = server
        cls.port = server.server_address[1]
        cls._thread = threading.Thread(target=server.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.shutdown()
        cls._server.server_close()
        cls._thread.join(timeout=2)
        if test_db_path.exists():
            test_db_path.unlink()

    def test_full_project_workflow(self) -> None:
        status, payload = _request(
            "POST",
            "/api/auth/register",
            {
                "email": "ana@example.com",
                "name": "Ana",
                "password": "senha-super-segura",
            },
            port=self.port,
        )
        self.assertEqual(status, 201, payload)
        token = payload["token"]

        status, payload = _request(
            "POST",
            "/api/auth/login",
            {"email": "ana@example.com", "password": "senha-super-segura"},
            port=self.port,
        )
        self.assertEqual(status, 200, payload)
        self.assertIn("token", payload)
        token = payload["token"]

        status, project = _request(
            "POST",
            "/api/projects",
            {"name": "Novo Álbum", "description": "Produção musical", "status": "active"},
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 201, project)
        project_id = project["id"]

        status, task = _request(
            "POST",
            f"/api/projects/{project_id}/tasks",
            {"title": "Mixagem", "description": "Preparar mix final", "status": "in_progress"},
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 201, task)
        task_id = task["id"]
        self.assertEqual(task["status"], "in_progress")

        status, task_list = _request(
            "GET",
            f"/api/projects/{project_id}/tasks",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, task_list)
        self.assertEqual(len(task_list["tasks"]), 1)

        status, updated_task = _request(
            "PATCH",
            f"/api/tasks/{task_id}",
            {"status": "completed"},
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, updated_task)
        self.assertEqual(updated_task["status"], "completed")

        status, asset = _request(
            "POST",
            f"/api/projects/{project_id}/assets",
            {"name": "Demo", "type": "audio", "url": "https://example.com/demo.mp3"},
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 201, asset)
        asset_id = asset["id"]

        status, project_detail = _request(
            "GET",
            f"/api/projects/{project_id}",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, project_detail)
        self.assertEqual(len(project_detail["tasks"]), 1)
        self.assertEqual(len(project_detail["assets"]), 1)

        status, _ = _request(
            "DELETE",
            f"/api/tasks/{task_id}",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, HTTPStatus.NO_CONTENT)

        status, task_list_after_delete = _request(
            "GET",
            f"/api/projects/{project_id}/tasks",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, task_list_after_delete)
        self.assertEqual(len(task_list_after_delete["tasks"]), 0)

        status, _ = _request(
            "DELETE",
            f"/api/assets/{asset_id}",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, HTTPStatus.NO_CONTENT)

        status, assets_after_delete = _request(
            "GET",
            f"/api/projects/{project_id}/assets",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, assets_after_delete)
        self.assertEqual(len(assets_after_delete["assets"]), 0)

        status, activity = _request(
            "GET",
            "/api/activity",
            token=token,
            port=self.port,
        )
        self.assertEqual(status, 200, activity)
        self.assertGreaterEqual(len(activity["activity"]), 3)

    def test_requires_authentication(self) -> None:
        status, payload = _request("GET", "/api/projects", port=self.port)
        self.assertEqual(status, 401)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()

