"""
A minimal A2A-style client.

Steps:
  1) Discover agent by GET /.well-known/agent.json
  2) Build a task payload with a unique id and a user message
  3) POST the task to /task/send
  4) Print the agent response (last message)
"""

import uuid
import requests

BASE_URL="http://127.0.0.1:5000"
AGENT_CARD_URL=f"{BASE_URL}/.well-known/agent-card.json"
TASK_SEND_URL=f"{BASE_URL}/task/send"

def discover_agent():
    """Fetch agent card and return it as a dict."""
    resp= requests.get(AGENT_CARD_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()

def send_task(task_payload):
    """Send a task to the agent and return the response."""
    resp = requests.post(TASK_SEND_URL, json=task_payload, timeout=10)
    resp.raise_for_status()
    return resp.json()

def main():
    # Step 1: Discover the agent
    agent_card = discover_agent()
    print("Discovered Agent Card:")
    print(agent_card)
    print(f"Discovered agent: {agent_card.get('name')}")
    print(f"Description: {agent_card.get('description')}")
    print(f"URL: {agent_card.get('url')}")
    print("-" * 60)

    # Step 2: Build a task payload
    task_id = str(uuid.uuid4())  # Unique task ID
    task_payload = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [
                {
                    "type": "text",
                    "text": "what time is it?"
                }
            ]
        }
    }
    
    # Step 3: Send the task to the agent
    try:
        response_data = send_task(task_payload)
    except requests.RequestException as e:
        raise SystemExit(f"Failed to send task: {e}")
    
    

    # Step 4: Print the agent response
    messages = response_data.get("messages", [])
    if not messages:
        print("No messages returned in response.")
        print("Raw response:", response_data)
        return
    last_msg = messages[-1]  # expected: agent message
    parts = last_msg.get("parts", [])

    if not parts:
        print("No parts found in the last message.")
        print("Last message:", last_msg)
        return

    # In this demo we expect a single text part
    text = parts[0].get("text")
    if text is None:
        print("No 'text' field found in the first part.")
        print("First part:", parts[0])
        return
    print(f"Agent says: {text}")
    

if __name__ == "__main__":
    main()