#!/usr/bin/env python3
"""Small self-contained API for the Night Dozhor fridge MVP.

The service intentionally uses SQLite and the Python standard library so it can
be started on an existing server without adding a framework.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


DB_PATH = os.getenv("FRIDGE_DB_PATH", os.path.join(os.path.dirname(__file__), "fridge.db"))
DEVICE_TOKEN = os.getenv("FRIDGE_DEVICE_TOKEN", "demo-device-token")
PORT = int(os.getenv("FRIDGE_PORT", "8080"))
LOCAL_ZONE = ZoneInfo(os.getenv("FRIDGE_TIMEZONE", "Europe/Moscow"))
MAX_BODY = 64 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return datetime.now(LOCAL_ZONE).date().isoformat()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
    return base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_b64, digest_b64 = encoded.split("$", 1)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 180_000)
        return secrets.compare_digest(actual, expected)
    except (ValueError, base64.binascii.Error):
        return False


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                face_id INTEGER UNIQUE,
                open_limit INTEGER NOT NULL DEFAULT 3,
                active_kcal_goal INTEGER NOT NULL DEFAULT 500,
                allow_from INTEGER NOT NULL DEFAULT 7,
                allow_until INTEGER NOT NULL DEFAULT 23,
                night_access INTEGER NOT NULL DEFAULT 0,
                product_rules TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_daily (
                user_id TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                active_calories REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'health_connect',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, activity_date),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS access_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                user_id TEXT,
                face_id INTEGER NOT NULL,
                access_type TEXT NOT NULL,
                result TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS enrollment_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                guest_access INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO devices(id, token, guest_access) VALUES (?, ?, 1)",
            ("fridge-001", DEVICE_TOKEN),
        )


def row_json(row: sqlite3.Row) -> dict:
    return dict(row)


def user_by_face(db: sqlite3.Connection, face_id: int) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE face_id = ?", (face_id,)).fetchone()


def build_policy(device_id: str) -> dict:
    current_date = today()
    current_hour = datetime.now(LOCAL_ZONE).hour
    with connect() as db:
        device = db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if device is None:
            raise KeyError("device_not_found")

        users = []
        for user in db.execute("SELECT * FROM users ORDER BY id"):
            health = db.execute(
                "SELECT active_calories FROM health_daily WHERE user_id = ? AND activity_date = ?",
                (user["id"], current_date),
            ).fetchone()
            active_calories = float(health["active_calories"]) if health else 0.0
            bonus = 2 if active_calories >= user["active_kcal_goal"] else 0
            opened = db.execute(
                "SELECT COUNT(*) AS count FROM access_events "
                "WHERE user_id = ? AND result = 'opened' AND created_at LIKE ?",
                (user["id"], current_date + "%"),
            ).fetchone()["count"]
            remaining = max(0, user["open_limit"] + bonus - opened)
            time_allowed = bool(user["night_access"]) or (
                user["allow_from"] <= current_hour < user["allow_until"]
            )
            allowed = remaining > 0 and time_allowed
            if not time_allowed:
                reason = "outside_schedule"
            elif remaining == 0:
                reason = "daily_limit_reached"
            elif bonus:
                reason = "activity_bonus"
            else:
                reason = "base_policy"
            users.append(
                {
                    "user_id": user["id"],
                    "name": user["name"],
                    "face_id": user["face_id"],
                    "allowed": allowed,
                    "reason": reason,
                    "remaining_opens": remaining,
                    "active_calories": active_calories,
                    "active_kcal_goal": user["active_kcal_goal"],
                    "product_rules": json.loads(user["product_rules"] or "{}"),
                }
            )

        pending = db.execute(
            "SELECT id, user_id, created_at FROM enrollment_commands "
            "WHERE device_id = ? AND status = 'pending' ORDER BY id LIMIT 1",
            (device_id,),
        ).fetchone()
        return {
            "device_id": device_id,
            "revision": int(datetime.now(timezone.utc).timestamp()),
            "guest_access": bool(device["guest_access"]),
            "users": users,
            "pending_enrollment": row_json(pending) if pending else None,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "NightDozhor/0.1"

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, payload: dict, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Device-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(encoded)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY:
            raise ValueError("request_too_large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def device_id_from_request(self, path: str) -> str:
        match = re.fullmatch(r"/api/devices/([^/]+)(?:/.*)?", path)
        if not match:
            raise ValueError("invalid_device_path")
        device_id = match.group(1)
        with connect() as db:
            device = db.execute("SELECT token FROM devices WHERE id = ?", (device_id,)).fetchone()
        if not device or not secrets.compare_digest(self.headers.get("X-Device-Token", ""), device["token"]):
            raise PermissionError("invalid_device_token")
        return device_id

    def do_OPTIONS(self) -> None:
        self.send_json({"ok": True})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/healthz":
                self.send_json({"ok": True, "service": "night-dozhor-fridge", "time": now_iso()})
                return
            match = re.fullmatch(r"/api/devices/([^/]+)/policy", path)
            if match:
                device_id = self.device_id_from_request(path)
                self.send_json(build_policy(device_id))
                return
            if path == "/api/demo/state":
                with connect() as db:
                    users = [row_json(row) for row in db.execute("SELECT id, name, face_id, open_limit, active_kcal_goal FROM users")]
                    events = [row_json(row) for row in db.execute("SELECT * FROM access_events ORDER BY id DESC LIMIT 20")]
                self.send_json({"users": users, "events": events})
                return
            self.send_json({"error": "not_found"}, 404)
        except PermissionError as error:
            self.send_json({"error": str(error)}, 401)
        except KeyError as error:
            self.send_json({"error": str(error)}, 404)
        except Exception as error:
            self.send_json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/login":
                with connect() as db:
                    user = db.execute("SELECT * FROM users WHERE name = ?", (str(body.get("name", "")),)).fetchone()
                if not user or not verify_password(str(body.get("password", "")), user["password_hash"]):
                    self.send_json({"error": "invalid_credentials"}, 401)
                    return
                self.send_json({"user_id": user["id"], "name": user["name"]})
                return

            if path == "/api/users":
                name = str(body.get("name", "")).strip()
                password = str(body.get("password", ""))
                if not name or len(password) < 4:
                    self.send_json({"error": "name_and_password_required"}, 422)
                    return
                user_id = "usr_" + secrets.token_hex(4)
                with connect() as db:
                    db.execute(
                        "INSERT INTO users(id, name, password_hash, face_id, open_limit, active_kcal_goal, "
                        "allow_from, allow_until, night_access, product_rules, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            user_id,
                            name,
                            hash_password(password),
                            body.get("face_id"),
                            max(1, int(body.get("open_limit", 3))),
                            max(0, int(body.get("active_kcal_goal", 500))),
                            max(0, min(23, int(body.get("allow_from", 7)))),
                            max(1, min(24, int(body.get("allow_until", 23)))),
                            int(bool(body.get("night_access", False))),
                            json.dumps(body.get("product_rules", {}), ensure_ascii=False),
                            now_iso(),
                        ),
                    )
                self.send_json({"user_id": user_id, "name": name}, 201)
                return

            if path == "/api/enrollment/start":
                user_id = str(body.get("user_id", ""))
                device_id = str(body.get("device_id", "fridge-001"))
                with connect() as db:
                    if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                        self.send_json({"error": "user_not_found"}, 404)
                        return
                    cursor = db.execute(
                        "INSERT INTO enrollment_commands(device_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
                        (device_id, user_id, now_iso()),
                    )
                self.send_json({"enrollment_id": cursor.lastrowid, "status": "pending"}, 201)
                return

            if path == "/api/health":
                user_id = str(body.get("user_id", ""))
                activity_date = str(body.get("date", today()))
                calories = max(0.0, float(body.get("active_calories", 0)))
                with connect() as db:
                    if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                        self.send_json({"error": "user_not_found"}, 404)
                        return
                    db.execute(
                        "INSERT INTO health_daily(user_id, activity_date, active_calories, source, updated_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, activity_date) DO UPDATE SET "
                        "active_calories=excluded.active_calories, source=excluded.source, updated_at=excluded.updated_at",
                        (user_id, activity_date, calories, str(body.get("source", "health_connect")), now_iso()),
                    )
                self.send_json({"ok": True, "user_id": user_id, "date": activity_date, "active_calories": calories})
                return

            match = re.fullmatch(r"/api/devices/([^/]+)/enrollment/complete", path)
            if match:
                device_id = self.device_id_from_request(path)
                user_id = str(body.get("user_id", ""))
                face_id = int(body.get("face_id", 0))
                with connect() as db:
                    db.execute("UPDATE users SET face_id = ? WHERE id = ?", (face_id, user_id))
                    db.execute(
                        "UPDATE enrollment_commands SET status = 'completed', completed_at = ? "
                        "WHERE device_id = ? AND user_id = ? AND status = 'pending'",
                        (now_iso(), device_id, user_id),
                    )
                self.send_json({"ok": True, "device_id": device_id, "user_id": user_id, "face_id": face_id})
                return

            match = re.fullmatch(r"/api/devices/([^/]+)/events", path)
            if match:
                device_id = self.device_id_from_request(path)
                face_id = int(body.get("face_id", 0))
                result = str(body.get("result", "opened"))
                access_type = "guest" if face_id == 0 else "known"
                with connect() as db:
                    user = user_by_face(db, face_id) if face_id else None
                    db.execute(
                        "INSERT INTO access_events(device_id, user_id, face_id, access_type, result, reason, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            device_id,
                            user["id"] if user else None,
                            face_id,
                            access_type,
                            result,
                            str(body.get("reason", "guest_access" if face_id == 0 else "policy")),
                            now_iso(),
                        ),
                    )
                self.send_json({"ok": True})
                return

            self.send_json({"error": "not_found"}, 404)
        except sqlite3.IntegrityError:
            self.send_json({"error": "name_or_face_id_already_exists"}, 409)
        except PermissionError as error:
            self.send_json({"error": str(error)}, 401)
        except Exception as error:
            self.send_json({"error": str(error)}, 400)


if __name__ == "__main__":
    init_db()
    print(f"Night Dozhor API listening on 0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
