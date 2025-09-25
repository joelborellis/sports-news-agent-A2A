# client_streaming.py
import os, json, asyncio, httpx
from dotenv import load_dotenv

load_dotenv()

#AGENT_URL = os.getenv("AGENT_URL", "http://localhost:10001")
AGENT_URL = os.getenv("AGENT_URL", "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io")
#STREAM_URL = f"{AGENT_URL}/rpc/v1/message:stream"
STREAM_URL = f"{AGENT_URL}"

def _parse_sse_lines(line: str):
    # Server-Sent Events: only care about `data: ...`
    if line.startswith("data: "):
        try:
            return json.loads(line[6:])
        except json.JSONDecodeError:
            return None
    return None

async def main():
    user_text = os.getenv("QUERY", "What were the MLB scores from last night?")
    payload = {
        "jsonrpc": "2.0",
        "method": "message.stream",
        "params": {
            "message": {
                "parts": [{"text": user_text}]
            }
        }
    }

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", STREAM_URL, json=payload, headers={
            "accept": "text/event-stream",
            "content-type": "application/json",
        }) as resp:
            resp.raise_for_status()
            print("Connected. Streaming events...\n")

            final_seen = False
            last_task_snapshot = None

            async for raw_line in resp.aiter_lines():
                if not raw_line:
                    continue
                parsed = _parse_sse_lines(raw_line)
                if not parsed:
                    continue

                # Each SSE event carries a JSON-RPC response
                result = parsed.get("result") or {}
                event = result.get("event")
                task  = result.get("task")
                if task:
                    last_task_snapshot = task  # keep most recent snapshot

                # Initial snapshot has no `event`
                if not event and task:
                    print(f"[snapshot] state={task['status']['state']}")
                    continue

                # Print event information (Message or TaskStatusUpdateEvent)
                et = (event.get("kind") or event.get("type") or "").lower()
                print(f"[event] kind={et or 'unknown'}")

                # If it's a Message, try to print text parts
                parts = None
                if event.get("role") == "agent" or event.get("role") == "user":
                    parts = event.get("parts") or []
                elif "status" in event:  # sometimes event may be wrapped differently
                    st = event["status"]
                    msg = st.get("message")
                    if msg:
                        parts = msg.get("parts") or []

                if parts:
                    for p in parts:
                        t = p.get("text")
                        if t:
                            print(t)

                # final?
                if event.get("final") is True:
                    final_seen = True
                    print("\n[final] received terminal event\n")
                    break

            # After stream ends, print artifacts if we have a snapshot
            if last_task_snapshot:
                arts = last_task_snapshot.get("artifacts", []) or []
                if arts:
                    print("--- Final artifacts ---")
                    for a in arts:
                        name = a.get("name", "artifact")
                        print(f"\n[{name}]")
                        for p in a.get("parts", []):
                            t = p.get("text")
                            if t:
                                print(t)

if __name__ == "__main__":
    asyncio.run(main())









