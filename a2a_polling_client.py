# client_polling.py
import os, sys, time, json, requests

BASE_URL = os.getenv("AGENT_URL", "https://sports-results-agent.ashydesert-9d471906.westus2.azurecontainerapps.io")
SEND_URL = f"{BASE_URL}/rpc/v1/message:send"
GET_URL  = f"{BASE_URL}/rpc/v1/tasks/get"
TERMINAL = {"completed", "failed", "canceled", "rejected"}

def main():
    user_text = "Who won the Steelers game?" if len(sys.argv) < 2 else " ".join(sys.argv[1:])

    # 1) Submit the task (non-stream)
    payload = {
        "jsonrpc": "2.0",
        "method": "message.send",
        "params": {
            "message": {
                "parts": [{"text": user_text}]
            }
        }
    }
    r = requests.post(SEND_URL, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    task = data["result"]["task"]
    task_id = task["id"]
    print(f"Submitted task: {task_id}")

    # 2) Poll until terminal
    last_state = task["status"]["state"]
    while last_state not in TERMINAL:
        time.sleep(0.6)
        poll = {
            "jsonrpc": "2.0",
            "method": "tasks.get",
            "params": {"taskId": task_id}
        }
        pr = requests.post(GET_URL, json=poll, timeout=30)
        pr.raise_for_status()
        pdata = pr.json()
        snap = pdata["result"]
        state = snap["status"]["state"]
        if state != last_state:
            print(f"state: {last_state} → {state}")
            last_state = state

    # 3) Print result
    print("\n=== FINAL SNAPSHOT ===")
    print(json.dumps(snap, indent=2))

    arts = snap.get("artifacts", []) or []
    if arts:
        body = []
        for a in arts:
            for p in a.get("parts", []):
                t = p.get("text")
                if t:
                    body.append(t)
        if body:
            print("\n--- Artifact text ---")
            print("\n".join(body))

if __name__ == "__main__":
    main()
