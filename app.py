# app.py
from __future__ import annotations
import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---- Your executor (kept as-is from your snippet)
from sports_agent_executor import SportsResultsAgent

# ---- A2A types (we'll serialize them defensively)
from a2a.types import (
    Message,
    TextPart,
    TaskState,
    TaskStatusUpdateEvent,
    AgentCard,
    AgentCapabilities,
    AgentSkill,
)

APP_NAME = os.getenv("APP_NAME", "SportsResultAgent")
AGENT_URL = os.getenv("AGENT_URL", "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io")

# ------------------------------------------------------------------------------
# Minimal adapters so your executor can run without pulling in full A2A server stack
# ------------------------------------------------------------------------------

class MinimalRequestContext:
    """Duck-typed RequestContext for your executor. Only what's actually used."""
    def __init__(self, task_id: str, user_text: str, context_id: Optional[str] = None) -> None:
        self.task_id = task_id
        self.context_id = context_id or task_id
        self._user_text = user_text

    def get_user_input(self) -> str:
        return self._user_text


class QueueEventQueue:
    """
    Simple async queue that matches the enqueue API your executor uses.
    We also expose an async iterator to drain events.
    """
    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    async def enqueue_event(self, event: Any) -> None:
        await self._q.put(event)

    async def aget(self) -> Any:
        return await self._q.get()

    async def aclose(self) -> None:
        await self._q.put(_SENTINEL)


_SENTINEL = object()

# ------------------------------------------------------------------------------
# In-memory task store (very small, for demo)
# ------------------------------------------------------------------------------

class TaskRecord:
    def __init__(self, task_id: str, context_id: str):
        self.id = task_id
        self.context_id = context_id
        self.state: str = TaskState.submitted
        self.history: List[dict] = []   # serialized Message objects
        self.artifacts: List[dict] = [] # one final text artifact built at completion
        self.last_status_message: Optional[dict] = None
        self.terminal: bool = False

TASKS: Dict[str, TaskRecord] = {}


# ------------------------------------------------------------------------------
# JSON-RPC helpers and serialization
# ------------------------------------------------------------------------------

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def rpc_result(payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "result": payload}

def rpc_error(message: str, code: int = -32000) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}}

def sse_event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")

def to_json(o: Any) -> Any:
    """
    Defensive serializer for a2a types:
    - Prefer .model_dump()
    - Then .dict()
    - Then __dict__ (shallow)
    - Fallback to str()
    """
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "dict"):
        try:
            return o.dict()
        except TypeError:
            pass
    if isinstance(o, (list, tuple)):
        return [to_json(x) for x in o]
    if isinstance(o, dict):
        return {k: to_json(v) for k, v in o.items()}
    if hasattr(o, "__dict__"):
        return {k: to_json(v) for k, v in vars(o).items() if not k.startswith("_")}
    return o


def extract_user_text(message_dict: dict) -> str:
    """
    Expecting params.message.parts[], with TextPart-like dicts:
      {"kind": "text" | omitted, "text": "..."}
    We'll join any text parts by newline.
    """
    parts = message_dict.get("parts") or []
    texts = []
    for p in parts:
        # your executor produces TextPart; clients may omit "kind"
        if isinstance(p, dict) and ("text" in p):
            texts.append(str(p["text"]))
        elif isinstance(p, TextPart):
            texts.append(p.text or "")
    return "\n".join(t for t in texts if t)


def build_task_snapshot(t: TaskRecord) -> dict:
    return {
        "id": t.id,
        "contextId": t.context_id,
        "status": {"state": t.state, "message": t.last_status_message},
        "artifacts": t.artifacts,
        "kind": "task",
    }


def maybe_make_text_artifact_from_history(t: TaskRecord) -> None:
    """
    Create a single text artifact from all agent messages.
    Do this once on completion/terminal.
    """
    if t.artifacts:
        return
    agent_texts = []
    for m in t.history:
        if m.get("role") == "agent":
            for p in m.get("parts", []):
                if isinstance(p, dict) and "text" in p and p["text"]:
                    agent_texts.append(p["text"])
    text = "\n".join(agent_texts).strip()
    if text:
        t.artifacts.append({
            "artifactId": str(uuid.uuid4()),
            "name": "result.txt",
            "description": "Concatenated agent output",
            "parts": [{"kind": "text", "text": text}],
        })

def sse_event_iter(lines):
    buf = []
    for line in lines:
        if not line:  # blank line => dispatch
            if buf:
                # join only "data:" lines; ignore comments/other fields
                payload = "\n".join(l[6:] for l in buf if l.startswith("data: "))
                if payload:
                    yield json.loads(payload)
                buf.clear()
            continue
        buf.append(line)


def is_terminal(state: str) -> bool:
    return state in {
        TaskState.completed,
        TaskState.failed,
        TaskState.canceled,
        TaskState.rejected,
    }

def _public_base_url_from_request(request: Request, explicit: str | None = None) -> str:
    """
    Resolve a public-facing base URL, respecting reverse proxies.
    Priority: explicit > X-Forwarded-* > request.url
    """
    if explicit:
        return explicit.rstrip("/")
    headers = request.headers
    scheme = headers.get("x-forwarded-proto", request.url.scheme)
    host   = headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc
    port   = headers.get("x-forwarded-port")
    # If port is present and not already in host, append it
    if port and ":" not in host and not (scheme == "https" and port == "443") and not (scheme == "http" and port == "80"):
        host = f"{host}:{port}"
    return f"{scheme}://{host}".rstrip("/")

def build_agent_card(base_url: str | None = None) -> AgentCard:
    base = (base_url or "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io").strip().rstrip("/")
    return AgentCard(
        name=APP_NAME,
        description="This agent provides sports results for various sports leagues such as MLB, NBA, NASCAR, and Golf.",
        # REST base (your app exposes /rpc/v1/message:send, /rpc/v1/message:stream, etc.)
        url=f"{base}/rpc/v1",
        version="0.1.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        # Snake_case property name in Python; will serialize to the spec via by_alias=True
        preferred_transport="REST",
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[
            AgentSkill(
                id="sports_results_agent",
                name="Sports Results Agent",
                description="Provides sports results from various sports leagues. Include scores, who won, and other relevant information.",
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
# FastAPI app + endpoints
# ------------------------------------------------------------------------------

app = FastAPI(title="A2A FastAPI App (Sports Results Agent)")

# Create one agent executor instance for the process
EXECUTOR = SportsResultsAgent()
EXECUTOR_INIT_LOCK = asyncio.Lock()
EXECUTOR_READY = False


async def ensure_executor_ready():
    global EXECUTOR_READY
    if EXECUTOR_READY:
        return
    async with EXECUTOR_INIT_LOCK:
        if not EXECUTOR_READY:
            # Your executor initializes its inner agent the first time execute() runs,
            # but we can do a proactive init by calling its private initializer:
            try:
                # Accessing your internal initializer if available
                if hasattr(EXECUTOR, "_ensure_initialized"):
                    await EXECUTOR._ensure_initialized()  # type: ignore[attr-defined]
            finally:
                EXECUTOR_READY = True


@app.post("/rpc/v1/message:send")
async def message_send(req: Request):
    """
    Non-streaming: create a Task, kick off execution in background, return Task snapshot.
    JSON-RPC body:
    {
      "id": "...",
      "jsonrpc": "2.0",
      "method": "message.send",
      "params": { "message": { "parts": [{"text":"..."}], "contextId": "optional" } }
    }
    """
    body = await req.json()
    params = (body or {}).get("params", {})
    message_dict = params.get("message") or {}
    context_id = message_dict.get("contextId") or str(uuid.uuid4())
    user_text = extract_user_text(message_dict)

    task_id = str(uuid.uuid4())
    t = TaskRecord(task_id, context_id)
    TASKS[task_id] = t

    # Record user message in history
    t.history.append({
        "messageId": message_dict.get("messageId", str(uuid.uuid4())),
        "role": "user",
        "parts": [{"kind": "text", "text": user_text}],
        "taskId": task_id,
        "contextId": context_id,
        "kind": "message",
    })

    # Kick off execution
    asyncio.create_task(_run_executor(task_id, context_id, user_text))

    return JSONResponse(rpc_result({"task": build_task_snapshot(t)}))


@app.post("/rpc/v1/message:stream")
@app.post("/")
async def message_stream(req: Request):
    """
    Streaming: create Task and stream A2A events as SSE JSON-RPC payloads.
    """
    print(f"AGENT_URL from / message_stream: {AGENT_URL}")
    body = await req.json()
    params = (body or {}).get("params", {})
    message_dict = params.get("message") or {}
    context_id = message_dict.get("contextId") or str(uuid.uuid4())
    user_text = extract_user_text(message_dict)

    task_id = str(uuid.uuid4())
    t = TaskRecord(task_id, context_id)
    TASKS[task_id] = t

    # Record user message
    t.history.append({
        "messageId": message_dict.get("messageId", str(uuid.uuid4())),
        "role": "user",
        "parts": [{"kind": "text", "text": user_text}],
        "taskId": task_id,
        "contextId": context_id,
        "kind": "message",
    })

    queue = QueueEventQueue()

    # In message_stream()

    async def event_gen():
        # initial snapshot first
        yield sse_event(rpc_result({"task": build_task_snapshot(t)}))

        # Start executor AFTER the client is listening.
        # IMPORTANT: we pass consume_locally=False so that only this generator reads the queue.
        asyncio.create_task(_run_executor(task_id, context_id, user_text, queue, consume_locally=False))

        # Drain the queue here and forward events + update TaskRecord
        while True:
            ev = await queue.aget()
            if ev is _SENTINEL:
                break

            _apply_event_to_task(t, ev)  # keep snapshot fresh

            payload = rpc_result({
                "event": to_json(ev),
                "task": build_task_snapshot(t),
            })
            yield sse_event(payload)

            # Close immediately on terminal events (executor also sends sentinel)
            if isinstance(ev, TaskStatusUpdateEvent) and getattr(ev, "final", False):
                break

    # Add recommended anti-buffering headers
    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # disables proxy buffering (nginx)
        },
    )



@app.post("/rpc/v1/tasks/get")
async def tasks_get(req: Request):
    """
    Polling endpoint to fetch the latest Task snapshot:
    JSON-RPC body: { "jsonrpc":"2.0", "method":"tasks.get", "params": {"taskId":"..."} }
    """
    body = await req.json()
    params = (body or {}).get("params", {})
    task_id = params.get("taskId")
    if not task_id or task_id not in TASKS:
        return JSONResponse(rpc_error("TaskNotFound"), status_code=404)
    t = TASKS[task_id]
    return JSONResponse(rpc_result(build_task_snapshot(t)))

@app.get("/.well-known/agent.json")
async def agent_card_endpoint(request: Request):
    """
    Public discovery endpoint for the Agent Card.
    Uses by_alias=True so keys like preferredTransport / defaultInputModes are camelCased.
    """
    #card = build_agent_card(_public_base_url_from_request(request))
    card = build_agent_card(AGENT_URL)
    return JSONResponse(
        content=card.model_dump(by_alias=True, exclude_none=True),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"}
    )

@app.get("/.well-known/agent-card.json")
async def agent_card_endpoint(request: Request):
    """
    Public discovery endpoint for the Agent Card.
    Uses by_alias=True so keys like preferredTransport / defaultInputModes are camelCased.
    """
    #card = build_agent_card(_public_base_url_from_request(request))
    card = build_agent_card(AGENT_URL)
    return JSONResponse(
        content=card.model_dump(by_alias=True, exclude_none=True),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"}
    )

# Optional mirror (handy for quick checks and local tooling)
@app.get("/rpc/v1/agent:card")
async def agent_card_mirror(request: Request):
    #card = build_agent_card(_public_base_url_from_request(request))
    card = build_agent_card(AGENT_URL)
    return JSONResponse(
        content=card.model_dump(by_alias=True, exclude_none=True),
        media_type="application/json",
        headers={"Cache-Control": "no-cache"}
    )

# ------------------------------------------------------------------------------
# Internal execution runner
# ------------------------------------------------------------------------------

# BEFORE
# async def _run_executor(task_id: string, context_id: string, user_text: str, queue: QueueEventQueue | None = None):

# AFTER (also fix type: str, not "string")
async def _run_executor(
    task_id: str,
    context_id: str,
    user_text: str,
    queue: QueueEventQueue | None = None,
    consume_locally: bool = True,   # <-- new
):
    await ensure_executor_ready()

    local_queue = queue or QueueEventQueue()

    # Initial status
    t = TASKS[task_id]
    t.state = TaskState.submitted
    t.last_status_message = {
        "role": "agent",
        "parts": [{"kind": "text", "text": "Task submitted"}],
        "timestamp": now_iso(),
    }

    context = MinimalRequestContext(task_id=task_id, user_text=user_text, context_id=context_id)

    # Only spin up the consumer when we're NOT streaming
    consumer_task = None
    if consume_locally:
        async def consume():
            while True:
                ev = await local_queue.aget()
                if ev is _SENTINEL:
                    break
                _apply_event_to_task(t, ev)
        consumer_task = asyncio.create_task(consume())

    try:
        await EXECUTOR.execute(context=context, event_queue=local_queue)
    finally:
        await local_queue.aclose()
        if consumer_task:
            await consumer_task

    if is_terminal(t.state):
        maybe_make_text_artifact_from_history(t)



def _apply_event_to_task(t: TaskRecord, ev: Any) -> None:
    # Two types expected from your executor: Message and TaskStatusUpdateEvent
    if isinstance(ev, Message):
        mj = to_json(ev)
        # Normalize TextPart list into plain dicts with text
        for p in mj.get("parts", []):
            if "text" in p and p.get("kind") is None:
                p["kind"] = "text"
        t.history.append(mj)

    elif isinstance(ev, TaskStatusUpdateEvent):
        sj = to_json(ev.status)
        state = sj.get("state") or t.state
        t.state = state
        # Attach human message if present
        msg = sj.get("message")
        if msg:
            # normalize parts.kind
            for p in msg.get("parts", []):
                if "text" in p and p.get("kind") is None:
                    p["kind"] = "text"
            t.history.append(msg)
            t.last_status_message = msg

        # On terminal, build a single text artifact from all agent messages
        if is_terminal(state):
            t.terminal = True
            maybe_make_text_artifact_from_history(t)

    else:
        # Unknown event—store stringified for visibility
        t.history.append({"role": "agent", "parts": [{"kind": "text", "text": f"[unknown event] {ev}"}]})


# ------------------------------------------------------------------------------
# Run: uvicorn app:app --reload --port 8000
# ------------------------------------------------------------------------------
