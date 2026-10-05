"""Starlette application exposing DeepSeek Harness over the A2A protocol."""

from __future__ import annotations

import json
import secrets

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .agent_card import build_agent_card
from .config import AGENT_CARD_PATH, Settings
from .dsh_runner import DshRunner
from .executor import DshAgentExecutor


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on every non-public route."""

    _PUBLIC_PATHS = frozenset({AGENT_CARD_PATH, "/healthz"})

    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._PUBLIC_PATHS:
            return await call_next(request)
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied, self._expected):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32001,
                        "message": "Unauthorized: missing or invalid bearer token.",
                    },
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def _healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def build_app(settings: Settings, runner: DshRunner | None = None) -> Starlette:
    """Assemble the A2A routes for the given settings.

    ``runner`` is injectable so contract tests can drive the executor without
    spawning a real ``dsh`` process.
    """
    agent_card = build_agent_card(settings)
    executor = DshAgentExecutor(settings, runner or DshRunner(settings))
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = [
        Route("/healthz", _healthz, methods=["GET"]),
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(
            request_handler,
            rpc_url="/",
            enable_v0_3_compat=settings.enable_v0_3_compat,
        ),
        *create_rest_routes(
            request_handler,
            enable_v0_3_compat=settings.enable_v0_3_compat,
        ),
    ]

    middleware = []
    if settings.token:
        middleware.append(Middleware(BearerTokenMiddleware, token=settings.token))

    return Starlette(routes=routes, middleware=middleware)


def agent_card_json(settings: Settings) -> str:
    """Serialize the Agent Card (handy for tests and ``--print-card``)."""
    from google.protobuf.json_format import MessageToDict

    card = build_agent_card(settings)
    return json.dumps(
        MessageToDict(card, preserving_proto_field_name=False),
        indent=2,
        ensure_ascii=False,
    )
