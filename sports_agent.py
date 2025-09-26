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

    async def get_complete_response(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Get complete response from the OpenAI Agent (non-streaming).

        Args:
            user_input (str): User input message.
            session_id (str): Unique identifier for the session (optional).

        Returns:
            dict: A dictionary containing the complete content and task completion status.
        """
        if not self.agent:
            return {
                "is_task_complete": True,
                "require_user_input": True,
                "content": "Agent not initialized. Please call initialize() first.",
            }

        try:
            # Collect all streaming content into a complete response
            print(f"Getting complete response for input: {user_input}")
            stream_result = Runner.run_streamed(
                self.agent,
                user_input,
            )

            complete_content = []
            event_count = 0
            
            async for event in stream_result.stream_events():
                event_count += 1
                #print(f"Event #{event_count}: {type(event).__name__}")
                
                # Look for ResponseTextDeltaEvent in raw_response_event
                if (
                    hasattr(event, "type")
                    and event.type == "raw_response_event"
                    and hasattr(event, "data")
                ):
                    data = event.data
                    data_type = type(data).__name__
                    #print(f"Raw response event data type: {data_type}")

                    # Extract text delta from ResponseTextDeltaEvent
                    if data_type == "ResponseTextDeltaEvent" and hasattr(data, "delta"):
                        delta_text = data.delta
                        #print(f"Delta text: '{delta_text}'")
                        if delta_text:  # Collect all content
                            complete_content.append(delta_text)

            # Combine all content into a single response
            full_content = "".join(complete_content)
            print(f"Complete response collected after {event_count} events: {full_content[:100]}...")
            
            return {
                "is_task_complete": True,
                "require_user_input": False,
                "content": full_content or "Task completed successfully.",
            }
            
        except Exception as e:
            print(f"Exception in get_complete_response: {e}")
            logger.error(f"Error during response generation: {e}")
            return {
                "is_task_complete": True,
                "require_user_input": False,
                "content": f"Error occurred: {str(e)}",
            }

    # Keep the original stream method for backward compatibility
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
            print(f"Starting to stream events for input: {user_input}")
            stream_result = Runner.run_streamed(
                self.agent,
                user_input,
            )

            event_count = 0
            completion_found = False
            
            async for event in stream_result.stream_events():
                event_count += 1
                print(f"Event #{event_count}: {type(event).__name__}")
                
                # Look for ResponseTextDeltaEvent in raw_response_event
                if (
                    hasattr(event, "type")
                    and event.type == "raw_response_event"
                    and hasattr(event, "data")
                ):
                    data = event.data
                    data_type = type(data).__name__
                    print(f"Raw response event data type: {data_type}")

                    # Extract text delta from ResponseTextDeltaEvent
                    if data_type == "ResponseTextDeltaEvent" and hasattr(data, "delta"):
                        delta_text = data.delta
                        print(f"Delta text: '{delta_text}'")
                        if delta_text:  # Only yield if there's actual content
                            yield {
                                "is_task_complete": False,
                                "require_user_input": False,
                                "content": delta_text,
                            }
                    
                    # Check for completion events
                    elif "Complete" in data_type or "Done" in data_type or "Finish" in data_type:
                        print(f"Found completion event: {data_type}")
                        completion_found = True

            print(f"Stream completed after {event_count} events. Completion found: {completion_found}")
            
            # Always send a final completion message
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": "" if completion_found else "Task completed successfully.",
            }
            
        except Exception as e:
            print(f"Exception in stream: {e}")
            logger.error(f"Error during streaming: {e}")
            # Handle error (rate limit logic removed)
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": f"Error occurred: {str(e)}",
            }

    async def cleanup(self):
        """Cleanup resources."""
        self.agent = None
# endregion