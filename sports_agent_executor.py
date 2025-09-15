import asyncio
import uuid
from typing import Any

from a2a.types import Message, TextPart, TaskState, TaskStatusUpdateEvent, TaskStatus
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

# Import your agent wrapper (this file is what you just asked me to create)
from sports_agent import OpenAIWebSearchAgent


class SportsResultsAgent(AgentExecutor):
    """
    A2A AgentExecutor that delegates execution to OpenAIWebSearchAgent.stream().
    It forwards streamed text deltas as A2A Message events and updates Task status.
    """

    def __init__(self) -> None:
        self._agent = OpenAIWebSearchAgent()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            async with self._init_lock:
                if not self._initialized:
                    await self._agent.initialize()
                    self._initialized = True

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Ensure agent is ready
        await self._ensure_initialized()

        # Announce work start
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.working,
                    message=Message(
                        messageId=str(uuid.uuid4()),
                        role="agent",
                        parts=[TextPart(text="Starting work…")]
                    )
                ),
                final=False,
                contextId=context.task_id,
                taskId=context.task_id,
            )
        )

        user_text: str = context.get_user_input() or ""

        # Stream deltas from your agent and forward them as A2A events
        async for chunk in self._agent.stream(user_text, session_id=context.task_id):
            print(f"Received chunk: {chunk}")
            content: str = str(chunk.get("content", ""))

            # Emit content as a Message (so clients can render text progressively)
            if content:
                await event_queue.enqueue_event(
                    Message(
                        messageId=str(uuid.uuid4()),
                        role="agent",
                        parts=[TextPart(text=content)]
                    )
                )

            # If upstream marks completion, finish with a final status
            if chunk.get("is_task_complete") is True:
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.completed,
                            message=Message(
                                messageId=str(uuid.uuid4()),
                                role="agent",
                                parts=[TextPart(text="All done!")]
                            )
                        ),
                        final=True,
                        contextId=context.task_id,
                        taskId=context.task_id,
                    )
                )
                return

        # Fallback: if the upstream never marked completion, complete now
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.completed,
                    message=Message(
                        messageId=str(uuid.uuid4()),
                        role="agent",
                        parts=[TextPart(text="Done (no explicit completion flag received).")]
                    )
                ),
                final=True,
                contextId=context.task_id,
                taskId=context.task_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Your upstream agent may or may not support cancellation; emit a canceled status here.
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=Message(
                        messageId=str(uuid.uuid4()),
                        role="agent",
                        parts=[TextPart(text="Canceled by request")]
                    )
                ),
                final=True,
                contextId=context.task_id,
                taskId=context.task_id,
            )
        )