"""Standalone Ficagent Flask application factory."""

import re

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .config import Config
from .modules.workbench.errors import GatewayError
from .modules.workbench.routes import workbench
from .modules.workbench.service import WorkbenchService
from .modules.financial_data.routes import financial_data
from .modules.news.routes import news
from .modules.news.service import NewsService


def create_app(config=None):
    """Create the local-only API and workbench server with isolated configuration."""
    app = Flask(__name__, static_folder=None)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    service = WorkbenchService(app.config)
    app.extensions["workbench_service"] = service
    app.register_blueprint(workbench)
    app.register_blueprint(financial_data)
    app.extensions["news_service"] = NewsService(app.config)
    app.register_blueprint(news)
    from .modules.agent_platform.service import AgentPlatform
    from .modules.agent_platform.routes import agents
    platform = AgentPlatform(service, app.config)
    from .modules.agent_platform.presets import register_builtin_agents
    register_builtin_agents(platform)
    service.platform = platform
    app.extensions["agent_platform"] = platform
    app.register_blueprint(agents)
    from .modules.capability_library.service import CapabilityLibrary
    from .modules.capability_library.routes import capabilities
    library = CapabilityLibrary(service, app.config)
    from .modules.capability_library.data_connections import DataConnections
    import json
    from pathlib import Path
    registry = json.loads((Path(__file__).parent / "modules/capability_library/data_connection_registry.json").read_text(encoding="utf-8"))
    library.data_connections = DataConnections(app, registry)
    service.capabilities = library
    app.extensions["capability_library"] = library
    app.register_blueprint(capabilities)
    if not (app.config.get('TESTING') and app.config.get('CAPABILITY_SKIP_PUBLIC_SEED')):
        from .modules.capability_library.catalog import seed_catalog
        app.extensions['capability_catalog_results'] = seed_catalog(library)

    from .modules.sub_agent.service import SubAgents
    from .modules.sub_agent.routes import sub_agents
    service.sub_agents = SubAgents(service, app.config)
    app.extensions['sub_agents'] = service.sub_agents
    app.register_blueprint(sub_agents)

    from .modules.evolution.service import Evolution
    from .modules.evolution.routes import evolution
    app.extensions['evolution'] = Evolution(service, app.config)
    app.register_blueprint(evolution)
    from .modules.agent_platform.asset_apps import AssetApps
    from .modules.agent_platform.asset_routes import asset_apps
    service.asset_apps = AssetApps(service, app.config)
    service.asset_apps.evolution = app.extensions['evolution']
    app.extensions['asset_apps'] = service.asset_apps
    app.register_blueprint(asset_apps)
    from .modules.agent_platform.app_community import AppCommunity
    from .modules.agent_platform.app_community_routes import community_apps
    app.extensions['app_community'] = AppCommunity(service.asset_apps)
    app.register_blueprint(community_apps)
    from .modules.agent_platform.authoring import Authoring
    from .modules.agent_platform.authoring_routes import authoring_sessions
    service.authoring = Authoring(service, app.config)
    app.extensions['authoring'] = service.authoring
    app.register_blueprint(authoring_sessions)
    from .modules.capability_library.portable_routes import portable_assets
    app.register_blueprint(portable_assets)
    from .modules.capability_library.community_routes import community_capabilities
    app.register_blueprint(community_capabilities)

    from .modules.explore.service import ExploreService
    from .modules.explore.routes import explore
    service.explore = ExploreService(service, app.extensions['news_service'], app.config)
    app.extensions['explore_service'] = service.explore
    app.register_blueprint(explore)
    from .modules.strategy_workshop.service import WorkshopService
    from .modules.strategy_workshop.routes import workshop
    service.workshop = WorkshopService(service, app.config)
    app.extensions['strategy_workshop'] = service.workshop
    app.register_blueprint(workshop)

    from .modules.app_workshop.service import AppWorkshopService
    from .modules.app_workshop.routes import app_workshop
    service.app_workshop = AppWorkshopService(platform, service.asset_apps)
    app.extensions['app_workshop'] = service.app_workshop
    app.register_blueprint(app_workshop)

    @app.before_request
    def protect_local_boundary():
        raw_uri = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI") or request.full_path
        if not raw_uri.startswith("/") or raw_uri.startswith("//") or "\\" in raw_uri:
            raise GatewayError("请求地址不正确。", 400)
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::\d+)?", request.host):
            raise GatewayError("本地服务仅允许本机访问。", 403)
        origin = request.headers.get("Origin")
        port = app.config["DESKTOP_PORT"]
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            raise GatewayError("该请求来源不受支持。", 403)

    @app.errorhandler(GatewayError)
    def gateway_error(error):
        response = {"error": str(error)}
        if getattr(error, "code", None) is not None:
            response["code"] = error.code
        return jsonify(response), error.status

    @app.after_request
    def prevent_api_cache(response):
        if request.path.startswith("/desktop/api/") and response.mimetype == "application/json":
            response.headers["Cache-Control"] = "no-store"
        if request.path == "/desktop/health":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "接口不存在。"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return jsonify({"error": "请求方法不受支持。"}), 405

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify({"error": "文件过大，单次上传上限为 30 MB。"}), 413

    @app.errorhandler(Exception)
    def internal_error(error):
        if isinstance(error, HTTPException):
            message = {400: "请求格式不正确。", 413: "文件过大，单次上传上限为 30 MB。"}.get(error.code, "请求失败。")
            return jsonify({"error": message}), error.code
        app.logger.exception("Ficagent request failed: %s", error)
        return jsonify({"error": "请求失败。"}), 500

    return app
