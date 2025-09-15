import logging
from collections.abc import AsyncIterable
from typing import Any

from agents import Agent, Runner, WebSearchTool  # external package you mentioned
from dotenv import load_dotenv
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

load_dotenv()

# region Response Format
class ResponseFormat(BaseModel):
    """A Response Format model to direct how the model should respond."""
    status: str = "input_required"
    message: str
# endregion


# region Azure AI Agent with MCP
class OpenAIWebSearchAgent:
    """Wraps OpenAI Agent with WebSearchTool to handle various tasks."""

    def __init__(self):
        self.agent = None

    async def initialize(self):
        """Initialize the OpenAI agent with WebSearchTool()."""
        try:
            self.agent = Agent(
                name="Sports Results Agent",
                instructions="You are a helpful agent that searches the web for sports results.",
                tools=[WebSearchTool()],
            )
            logger.info("OpenAI Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI Agent: {e}")
            await self.cleanup()
            raise

    async def stream(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> AsyncIterable[dict[str, Any]]:
        """Stream responses from the OpenAI Agent.

        Args:
            user_input (str): User input message.
            session_id (str): Unique identifier for the session (optional).

        Yields:
            dict: A dictionary containing the content and task completion status.
        """
        if not self.agent:
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": "Agent not initialized. Please call initialize() first.",
            }
            return

        try:
            # Use the stream_events() method to get async iterable events
            stream_result = Runner.run_streamed(
                self.agent,
                user_input,
            )

            async for event in stream_result.stream_events():
                # Look for ResponseTextDeltaEvent in raw_response_event
                if (
                    hasattr(event, "type")
                    and event.type == "raw_response_event"
                    and hasattr(event, "data")
                ):
                    data = event.data
                    data_type = type(data).__name__

                    # Extract text delta from ResponseTextDeltaEvent
                    if data_type == "ResponseTextDeltaEvent" and hasattr(data, "delta"):
                        delta_text = data.delta
                        if delta_text:  # Only yield if there's actual content
                            yield {
                                "is_task_complete": False,
                                "require_user_input": False,
                                "content": delta_text,
                            }

            # Final completion message
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": "Task completed successfully.",
            }
        except Exception as e:
            # Handle error (rate limit logic removed)
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": str(e),
            }

    async def cleanup(self):
        """Cleanup resources."""
        self.agent = None
# endregion