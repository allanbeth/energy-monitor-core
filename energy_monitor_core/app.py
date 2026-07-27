from __future__ import annotations

import json
import os
import uuid
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from .auth import build_basic_token, parse_basic_token
from .backups import BackupService
from .config import ConfigManager
from .dependencies import ensure_core_dependencies, ensure_module_dependencies
from .logging import get_logger
from .live_data import SENSOR_DATA_STORE
from .module_profiles import get_module_profile
from .modules import ModuleRuntimeManager
from .mqtt import MQTTSubscriber, MQTTPublisher


logger = get_logger(__name__)


def create_app(app_root: Optional[Path] = None) -> Flask:
    root = Path(app_root or Path(__file__).resolve().parents[1]).resolve()
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "energy-monitor-core-secret")
    app.config["APP_ROOT"] = str(root)
    app.config["APP_PORT"] = int(os.environ.get("FLASK_PORT", "8030"))
    app.config["APP_HOST"] = os.environ.get("FLASK_HOST", "0.0.0.0")
    app.config["SERVER_INSTANCE_ID"] = uuid.uuid4().hex
    app.config["BASE_HREF"] = _resolve_base_href()

    config_manager = ConfigManager(root)
    app.extensions["config_manager"] = config_manager

    ensure_core_dependencies()

    active_modules = list(config_manager.get_active_modules().keys())
    if active_modules:
        ensure_module_dependencies(config_manager, active_modules)

    backup_service = BackupService(config_manager)
    runtime_manager = ModuleRuntimeManager(root, config_manager, SENSOR_DATA_STORE)
    runtime_manager.sync_from_config()

    mqtt_publisher = None
    mqtt_subscriber = None
    mqtt_config = config_manager.get_config().get("mqtt", {})
    if bool(mqtt_config.get("enabled", False)):
        mqtt_publisher = MQTTPublisher(mqtt_config)
        mqtt_subscriber = MQTTSubscriber(mqtt_config, SENSOR_DATA_STORE)

    app.extensions["backup_service"] = backup_service
    app.extensions["runtime_manager"] = runtime_manager
    app.extensions["mqtt_publisher"] = mqtt_publisher
    app.extensions["mqtt_subscriber"] = mqtt_subscriber
    app.extensions["live_data_store"] = SENSOR_DATA_STORE

    _start_scheduler_threads(app, config_manager, backup_service, runtime_manager, mqtt_publisher)
    _register_routes(app, config_manager, backup_service, runtime_manager, mqtt_publisher)
    return app


def _resolve_base_href() -> str:
    prefix = str(os.environ.get("FLASK_BASE_PATH") or os.environ.get("SCRIPT_NAME") or "").strip()
    if not prefix:
        prefix = "/"
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/") + "/"


def _start_scheduler_threads(app: Flask, config_manager: ConfigManager, backup_service: BackupService, runtime_manager: ModuleRuntimeManager, mqtt_publisher: Optional[MQTTPublisher]) -> None:
    def backup_loop() -> None:
        while True:
            try:
                general = config_manager.get_config().get("general", {})
                if bool(general.get("auto_backup_enabled", True)):
                    backup_service.backup_core()
                    for module_name in config_manager.get_active_modules().keys():
                        backup_service.backup_module(module_name)
                time.sleep(24 * 60 * 60)
            except Exception as error:
                logger.exception("Auto-backup loop failed: %s", error)
                time.sleep(300)

    def mqtt_loop() -> None:
        while True:
            try:
                if mqtt_publisher and mqtt_publisher.get_connection_status().get("state") == "Connected":
                    live_data = runtime_manager.get_full_live_data()
                    aggregate = runtime_manager.get_aggregate_totals()
                    mqtt_publisher.publish_active_modules(config_manager.get_active_modules(), live_data, config_manager.get_config().get("mqtt", {}))
                    mqtt_publisher.publish_hub_totals(aggregate)
                time.sleep(30)
            except Exception as error:
                logger.exception("MQTT loop failed: %s", error)
                time.sleep(60)

    threading.Thread(target=backup_loop, name="auto-backup-loop", daemon=True).start()
    if mqtt_publisher:
        threading.Thread(target=mqtt_loop, name="mqtt-loop", daemon=True).start()


def _register_routes(app: Flask, config_manager: ConfigManager, backup_service: BackupService, runtime_manager: ModuleRuntimeManager, mqtt_publisher: Optional[MQTTPublisher]) -> None:
    instance_id = app.config["SERVER_INSTANCE_ID"]

    def _base_context() -> Dict[str, Any]:
        active_modules = config_manager.get_active_modules()
        return {
            "base_href": app.config.get("BASE_HREF", "/"),
            "server_instance_id": instance_id,
            "sensor_type_options": ["solar", "wind", "battery", "charger"],
            "module_nav": [
                {
                    "name": module_name,
                    "title": module_data.get("definitions", {}).get("sensor-title", module_name.title()),
                    "icon": module_data.get("definitions", {}).get("icon", "fa-circle-dot"),
                    "module_url": f"/module/{module_name}",
                    "settings_url": f"/settings/module/{module_name}",
                }
                for module_name, module_data in active_modules.items()
            ],
        }

    def _is_authenticated() -> bool:
        if not bool(config_manager.get_config().get("auth", {}).get("enabled", True)):
            return True

        if session.get("server_instance_id") not in (None, instance_id):
            session.clear()
            return False

        auth_token = request.headers.get("Authorization", "")
        if auth_token:
            username, password, token_instance_id = parse_basic_token(auth_token)
            if username and token_instance_id == instance_id and config_manager.verify_auth_credentials(username, password):
                session["authenticated"] = True
                session["username"] = username
                session["server_instance_id"] = instance_id
                return True

        if session.get("authenticated") and session.get("server_instance_id") == instance_id:
            return True

        return False

    def require_auth(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any):
            if _is_authenticated():
                return view(*args, **kwargs)
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("index"))

        return wrapper

    @app.before_request
    def enforce_auth() -> Optional[Response]:
        if request.path.startswith("/static/"):
            return None
        if request.path in {"/api/auth/login", "/health"}:
            return None
        if request.method == "GET" and request.path == "/":
            return None
        if request.method == "GET" and request.path.startswith("/module/"):
            return None
        if _is_authenticated():
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return None

    @app.after_request
    def attach_instance_header(response: Response):
        response.headers["X-Monitor-Server-Instance-Id"] = instance_id
        return response

    @app.route("/health")
    def health() -> Any:
        return jsonify({"status": "ok", "port": app.config.get("APP_PORT", 8030)})

    @app.route("/")
    def index() -> Any:
        if not _is_authenticated():
            return render_template(
                "login.html",
                app_title="Energy Monitor Core",
                public_auth=config_manager.get_auth_public_config(),
                page_name="login",
                **_base_context(),
            )

        active_modules = config_manager.get_active_modules()
        dashboard_modules = []
        for module_name, module_data in active_modules.items():
            module_snapshot = runtime_manager.get_module_snapshot(module_name)
            dashboard_modules.append({
                "module": module_name,
                **module_data,
                "sensor_summary": module_snapshot.get("sensor_type_summary", {}),
                "module_url": f"module/{module_name}",
                "settings_url": f"settings/module/{module_name}",
                "connection_status": module_snapshot.get("status", "disconnected"),
                "connected_sensor_count": module_snapshot.get("connected_sensor_count", 0),
            })

        return render_template(
            "dashboard.html",
            app_title="Energy Monitor Core",
            modules=dashboard_modules,
            status=config_manager.get_public_status(runtime_manager),
            dashboard_trends=runtime_manager.get_aggregate_trends(),
            username=session.get("username", config_manager.get_auth_public_config().get("username")),
            page_name="dashboard",
            **_base_context(),
        )

    @app.route("/settings")
    def core_settings() -> Any:
        if not _is_authenticated():
            return redirect(url_for("index"))

        core_config = config_manager.get_config()
        active_modules = config_manager.get_active_modules()

        mqtt_enabled = bool((core_config.get("mqtt") or {}).get("enabled", False))
        mqtt_connection_state = "Disabled"
        if mqtt_enabled:
            mqtt_connection_state = "Disconnected"
        if mqtt_publisher:
            mqtt_connection_state = str((mqtt_publisher.get_connection_status() or {}).get("state") or mqtt_connection_state)

        webserver_enabled = bool((core_config.get("webserver") or {}).get("enabled", True))
        webserver_connection_state = "Connected" if webserver_enabled else "Disabled"

        def _connection_class(state: str) -> str:
            normalized = str(state or "").strip().lower()
            if normalized == "connected":
                return "status-connected"
            if normalized in {"disabled", "partial", "warning"}:
                return "status-partial"
            return "status-disconnected"

        return render_template(
            "settings.html",
            app_title="Core Settings",
            page_name="core-settings",
            core_config=core_config,
            modules=active_modules,
            status=config_manager.get_public_status(runtime_manager),
            mqtt_connection_state=mqtt_connection_state,
            mqtt_connection_class=_connection_class(mqtt_connection_state),
            webserver_connection_state=webserver_connection_state,
            webserver_connection_class=_connection_class(webserver_connection_state),
            username=session.get("username", config_manager.get_auth_public_config().get("username")),
            **_base_context(),
        )

    @app.route("/module/<module_name>")
    def module_page(module_name: str) -> Any:
        if not _is_authenticated():
            return redirect(url_for("index"))

        if module_name not in config_manager.get_active_modules():
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404

        module_data = config_manager.get_module_payload(module_name)
        module_snapshot = runtime_manager.get_module_snapshot(module_name)
        module_profile = get_module_profile(module_name)

        return render_template(
            "module.html",
            app_title=f"{module_profile.get('title', module_name.title())} Live",
            module=module_data,
            module_name=module_name,
            module_profile=module_profile,
            module_snapshot=module_snapshot,
            module_history=runtime_manager.get_module_history(module_name),
            username=session.get("username", config_manager.get_auth_public_config().get("username")),
            page_name="module",
            **_base_context(),
        )

    @app.route("/settings/module/<module_name>")
    def module_settings_page(module_name: str) -> Any:
        if not _is_authenticated():
            return redirect(url_for("index"))

        active_modules = config_manager.get_active_modules()
        if module_name not in active_modules:
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404

        module_payload = active_modules[module_name]
        module_title = module_payload.get("definitions", {}).get("settings-title", f"{module_name.title()} Settings")
        return render_template(
            "module_settings.html",
            app_title=module_title,
            page_name="module-settings",
            module_name=module_name,
            module_payload=module_payload,
            status=config_manager.get_public_status(runtime_manager),
            username=session.get("username", config_manager.get_auth_public_config().get("username")),
            **_base_context(),
        )

    @app.route("/api/auth/login", methods=["POST"])
    def login() -> Any:
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        if not username or not password:
            return jsonify({"error": "Username and password required"}), 400

        if not config_manager.verify_auth_credentials(username, password):
            return jsonify({"error": "Invalid credentials"}), 401

        session["authenticated"] = True
        session["username"] = username
        session["server_instance_id"] = instance_id
        return jsonify({"status": "success", "message": "Authentication successful", "auth_token": build_basic_token(username, password, instance_id)})

    @app.route("/api/auth/logout", methods=["POST"])
    @require_auth
    def logout() -> Any:
        session.clear()
        return jsonify({"status": "success"})

    @app.route("/api/status", methods=["GET"])
    @require_auth
    def status() -> Any:
        return jsonify(config_manager.get_public_status(runtime_manager))

    @app.route("/api/config", methods=["GET", "PUT"])
    @require_auth
    def core_config() -> Any:
        if request.method == "GET":
            return jsonify(config_manager.get_config())

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Configuration payload required"}), 400

        if not config_manager.update_core_config(payload):
            return jsonify({"error": "Failed to update core configuration"}), 500

        active_modules = list(config_manager.get_active_modules().keys())
        if active_modules:
            ensure_module_dependencies(config_manager, active_modules)
        runtime_manager.sync_from_config()
        return jsonify({"status": "success", "message": "Core configuration updated"})

    @app.route("/api/modules", methods=["GET"])
    @require_auth
    def modules() -> Any:
        return jsonify(config_manager.get_active_modules())

    @app.route("/api/modules/<module_name>", methods=["GET", "PUT"])
    @require_auth
    def module_config(module_name: str) -> Any:
        if module_name not in config_manager.get_active_modules():
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404

        if request.method == "GET":
            return jsonify(config_manager.get_module_payload(module_name))

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Module configuration payload required"}), 400

        module_config = payload.get("module_config")
        sensor_config = payload.get("sensor_config")
        if not config_manager.update_module_config(module_name, module_config, sensor_config):
            return jsonify({"error": "Failed to update module configuration"}), 500

        runtime_manager.refresh_module(module_name)
        return jsonify({"status": "success", "message": f"{module_name} updated"})

    @app.route("/api/modules/<module_name>/history", methods=["GET"])
    @require_auth
    def module_history(module_name: str) -> Any:
        if module_name not in config_manager.get_active_modules():
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404
        return jsonify({"module": module_name, "history": runtime_manager.get_module_history(module_name)[-20:]})

    @app.route("/api/modules/<module_name>/snapshot", methods=["GET"])
    @require_auth
    def module_snapshot(module_name: str) -> Any:
        if module_name not in config_manager.get_active_modules():
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404
        return jsonify(runtime_manager.get_module_snapshot(module_name))

    @app.route("/api/live/<module_name>", methods=["POST"])
    @require_auth
    def ingest_live_data(module_name: str) -> Any:
        if module_name not in config_manager.get_module_names():
            return jsonify({"error": f"Unknown module: {module_name}"}), 404

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Live payload required"}), 400

        SENSOR_DATA_STORE.ingest_module_snapshot(module_name, payload)
        runtime_manager.refresh_module(module_name)
        return jsonify({"status": "success"})

    @app.route("/api/modules/<module_name>/activate", methods=["POST"])
    @require_auth
    def activate_module(module_name: str) -> Any:
        if module_name not in config_manager.get_module_names():
            return jsonify({"error": f"Unknown module: {module_name}"}), 404

        config_manager.set_module_status(module_name, True)
        ensure_module_dependencies(config_manager, [module_name])
        runtime_manager.sync_from_config()
        return jsonify({"status": "success", "message": f"{module_name} activated"})

    @app.route("/api/modules/<module_name>/deactivate", methods=["POST"])
    @require_auth
    def deactivate_module(module_name: str) -> Any:
        if module_name not in config_manager.get_module_names():
            return jsonify({"error": f"Unknown module: {module_name}"}), 404

        config_manager.set_module_status(module_name, False)
        runtime_manager.sync_from_config()
        return jsonify({"status": "success", "message": f"{module_name} deactivated"})

    @app.route("/api/auth/credentials", methods=["PUT"])
    @require_auth
    def update_credentials() -> Any:
        payload = request.get_json(silent=True) or {}
        result = config_manager.update_auth_credentials(
            payload.get("new_username", ""),
            payload.get("new_password", ""),
            payload.get("current_password", ""),
        )
        if not result.get("ok"):
            return jsonify({"error": result.get("message", "Unable to update credentials")}), 400
        return jsonify({"status": "success", "message": result.get("message", "Credentials updated")})

    @app.route("/api/backups", methods=["GET"])
    @require_auth
    def backups() -> Any:
        return jsonify({
            "core": backup_service.list_backups("core"),
            "modules": {module_name: backup_service.list_backups(module_name) for module_name in config_manager.get_active_modules().keys()},
        })

    @app.route("/api/backups/core", methods=["POST"])
    @require_auth
    def backup_core() -> Any:
        return jsonify({"status": "success", "backup": backup_service.backup_core()})

    @app.route("/api/backups/all", methods=["POST"])
    @require_auth
    def backup_all() -> Any:
        return jsonify(backup_service.backup_all())

    @app.route("/api/webserver/restart", methods=["POST"])
    @require_auth
    def restart_webserver() -> Any:
        def _exit_later() -> None:
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=_exit_later, name="webserver-restart", daemon=True).start()
        return jsonify({"status": "success", "message": "Webserver restarting"})

    @app.route("/api/backups/module/<module_name>", methods=["POST"])
    @require_auth
    def backup_module(module_name: str) -> Any:
        if module_name not in config_manager.get_active_modules():
            return jsonify({"error": f"Unknown or inactive module: {module_name}"}), 404
        return jsonify({"status": "success", "backup": backup_service.backup_module(module_name)})

    @app.route("/api/backups/core/<backup_name>/restore", methods=["POST"])
    @require_auth
    def restore_core(backup_name: str) -> Any:
        if not backup_service.restore_core(backup_name):
            return jsonify({"error": "Core restore failed"}), 404
        runtime_manager.sync_from_config()
        return jsonify({"status": "success"})

    @app.route("/api/backups/module/<module_name>/<backup_name>/restore", methods=["POST"])
    @require_auth
    def restore_module(module_name: str, backup_name: str) -> Any:
        if not backup_service.restore_module(module_name, backup_name):
            return jsonify({"error": "Module restore failed"}), 404
        runtime_manager.sync_from_config()
        return jsonify({"status": "success"})

    @app.route("/api/logs/recent", methods=["GET"])
    @require_auth
    def recent_logs() -> Any:
        module_name = request.args.get("module")
        if module_name:
            return jsonify({"module": module_name, "lines": _read_recent_log_lines(Path(app.config["APP_ROOT"]) / "logs" / "modules" / f"{module_name}.log", 120)})
        return jsonify({"module": None, "lines": _read_recent_log_lines(Path(app.config["APP_ROOT"]) / "logs" / "app.log", 120)})


def _read_recent_log_lines(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return "Logs will appear here after the app starts."

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return "Unable to read log file."
