from __future__ import annotations

import httpx
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi.responses import JSONResponse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryTaskStore,
    InMemoryPushNotificationConfigStore,
    BasePushNotificationSender,
)
from a2a.server.events import InMemoryQueueManager
from a2a.server.agent_execution import SimpleRequestContextBuilder
from a2a.server.apps.rest import A2ARESTFastAPIApplication

# Import your executor from echo_agent.py
from sports_agent_executor import SportsResultsAgent

APP_NAME = os.getenv("APP_NAME", "SportsResultAgent")

# ------------------------------------------------------------------------------
# Agent Card
# ------------------------------------------------------------------------------


def build_agent_card(base_url: str | None = None) -> AgentCard:
    base = (base_url or "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io").strip().rstrip("/")
    return AgentCard(
        name=APP_NAME,
        description="This agent provides sports results for various sports leagues such as MLB, NBA, NASCAR, and Golf.",
        url=f"{base}/rpc/v1",                    # <— REST base, not root
        version="0.1.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        # Tell the client to use REST transport (field name in Python is snake_case)
        preferred_transport="REST",              # <— THIS flips you off JSONRPC
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[
            AgentSkill(
                id="sports_results_agent",
                name="Sports Results Agent",
                description="Provides sports results from various sports leagues.  Include scores, who won, and other relevant information.",
                tags=["mlb", "nba", "nascar", "golf", "college football"],
                examples=[
                    "Show score for Pirates game last night",
                    "What was the final score of the game 7 NBA finals and who won?",
                    "Who won the 2025 US Golf Open Championship and where was it played?",
                ],
            )
        ],
    )



# ------------------------------------------------------------------------------
# Lifespan / state
# ------------------------------------------------------------------------------


class AppState:
    pass

state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    state.http = httpx.AsyncClient(timeout=30.0)
    state.task_store = InMemoryTaskStore()
    state.queue_manager = InMemoryQueueManager()
    state.push_store = InMemoryPushNotificationConfigStore()
    state.push_sender = BasePushNotificationSender(
        httpx_client=state.http, config_store=state.push_store
    )

    state.request_handler = DefaultRequestHandler(
        agent_executor=SportsResultsAgent(),
        task_store=state.task_store,
        queue_manager=state.queue_manager,
        push_config_store=state.push_store,
        push_sender=state.push_sender,
        request_context_builder=SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=state.task_store
        ),
    )
    try:
        yield
    finally:
        await state.http.aclose()


# ------------------------------------------------------------------------------
# Build app
# ------------------------------------------------------------------------------


def create_app() -> FastAPI:
    agent_card = build_agent_card()

    # Synchronously initialize dependencies for request_handler
    http = httpx.AsyncClient(timeout=30.0)
    task_store = InMemoryTaskStore()
    queue_manager = InMemoryQueueManager()
    push_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(
        httpx_client=http, config_store=push_store
    )
    request_handler = DefaultRequestHandler(
        agent_executor=SportsResultsAgent(),
        task_store=task_store,
        queue_manager=queue_manager,
        push_config_store=push_store,
        push_sender=push_sender,
        request_context_builder=SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=task_store
        ),
    )

    # Store in state for lifespan cleanup
    state.http = http
    state.task_store = task_store
    state.queue_manager = queue_manager
    state.push_store = push_store
    state.push_sender = push_sender
    state.request_handler = request_handler

    app_builder = A2ARESTFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    app = app_builder.build(
        agent_card_url="/.well-known/agent.json",
        rpc_url="/rpc",
        lifespan=lifespan,
        title=APP_NAME,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {"ok": True, "name": APP_NAME}


    @app.get("/.well-known/agent.json")
    async def agent_card_endpoint():
        card = build_agent_card()
        return JSONResponse(content=card.model_dump())

    # Compatibility endpoint for clients expecting agent-card.json
    @app.get("/.well-known/agent-card.json")
    async def agent_card_compat():
        card = build_agent_card()
        return JSONResponse(content=card.model_dump())

    return app


app = create_app()

print("Registered routes:")
for route in app.routes:
    print(route.path, route.methods)