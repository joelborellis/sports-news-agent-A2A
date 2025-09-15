import asyncio, uuid, json, httpx

#BASE = "http://localhost:10001"
BASE = "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io/"
URL = f"{BASE}/rpc/v1/message:stream"

async def main():
    payload = {
        "message": {
            "messageId": str(uuid.uuid4()),
            "role": 1,
            "content": [{"text": "Show score for Pirates game last night"}],
        }
    }
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("POST", URL, headers={"Accept":"text/event-stream","Content-Type":"application/json"}, json=payload) as resp:
            print("Status:", resp.status_code, "| CT:", resp.headers.get("content-type"))
            async for line in resp.aiter_lines():
                if line and line.startswith("data:"):
                    payload = line[5:].lstrip()
                    try:
                        obj = json.loads(payload)
                        print("[data]", json.dumps(obj, indent=2))
                    except Exception:
                        print("[data]", payload)

if __name__ == "__main__":
    asyncio.run(main())








