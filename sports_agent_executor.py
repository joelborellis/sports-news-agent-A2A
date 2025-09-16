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
        # Switch back to real agent
        # self._agent = MockStreamingAgent() 
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
        print(f"Starting execute for task: {context.task_id}")
        
        try:
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
            print(f"Processing user input: {user_text}")

            # Get complete response from agent (non-streaming approach)
            print(f"Getting complete response from agent...")
            
            try:
                # Use the new non-streaming method
                response = await self._agent.get_complete_response(user_text, session_id=context.task_id)
                print(f"Received complete response: {response}")
                
                content: str = str(response.get("content", ""))
                
                # Send the complete response as a single message
                if content:
                    print(f"Enqueuing complete message: {content[:100]}...")
                    await event_queue.enqueue_event(
                        Message(
                            messageId=str(uuid.uuid4()),
                            role="agent",
                            parts=[TextPart(text=content)]
                        )
                    )
                    print(f"Successfully enqueued complete response")

            except Exception as response_error:
                print(f"ERROR getting complete response: {response_error}")
                import traceback
                traceback.print_exc()
                # Set content to error message
                content = f"Error occurred: {str(response_error)}"
                await event_queue.enqueue_event(
                    Message(
                        messageId=str(uuid.uuid4()),
                        role="agent",
                        parts=[TextPart(text=content)]
                    )
                )

            # Always send completion status (moved outside the try block)
            print(f"Task completed - sending completion status")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=Message(
                            messageId=str(uuid.uuid4()),
                            role="agent",
                            parts=[TextPart(text="Task completed successfully!")]
                        )
                    ),
                    final=True,
                    contextId=context.task_id,
                    taskId=context.task_id,
                )
            )
            print(f"Execute completed successfully")
            return
        except Exception as e:
            print(f"FATAL ERROR in execute: {e}")
            import traceback
            traceback.print_exc()
            
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=Message(
                            messageId=str(uuid.uuid4()),
                            role="agent",
                            parts=[TextPart(text=f"Error occurred: {str(e)}")]
                        )
                    ),
                    final=True,
                    contextId=context.task_id,
                    taskId=context.task_id,
                )
            )
            return

        # Fallback completion has been removed since we always complete in the main try block

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