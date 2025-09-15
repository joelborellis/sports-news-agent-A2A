import uuid, httpx, json

BASE = "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io/"
SEND_URL = f"{BASE}/rpc/v1/message:send"

payload = {
    "message": {
        "messageId": str(uuid.uuid4()),
        "role": 1,
        "content": [{"text": "Show score for Pirates game last night"}],
    }
}

r = httpx.post(SEND_URL, json=payload, timeout=30)
print("Status:", r.status_code)
print(r.text)
