"""
A minimal A2A-style "A2S server" implemented with Flask.

Endpoints:
  1) GET  /.well-known/agent.json   -> Agent Card (discovery)
  2) POST /task/send                -> Accept a task and respond with current time

This is intentionally simple:
- No auth
- No streaming responses
- No push notifications
"""

from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

HOST="127.0.0.1"
PORT=5000
BASE_URL=f"http://{HOST}:{PORT}"


"""The Agent Card is a JSON document that serves as a digital business card 
for initial discovery and interaction setup."""
@app.route("/.well-known/agent-card.json", methods=["GET"])
def agent_card():
    """
        Agent Card: metadata used by clients to discover the agent and learn:
        - name/description
        - base URL
        - capabilities
        - endpoints (in this simplified example, we just document /task/send)
    """
    card={
        "name": "Tell Time Server",
        "description":"A simple A2A server that tells the current time.",
        "url": BASE_URL,
        "version":"1.0",
        "capabilities":{
            "streaming": False,
            "pushNotifications": False
        },
        "endpoints":{ 
            "taskSend": f"{BASE_URL}/task/send"
        },
        "authentication":{
            "type": "none"
        }
    }
    return jsonify(card),200

@app.post("/task/send")
def handle_task():
    """
    Receives a task payload and returns a response payload.

    Expected request shape (minimal):
    {
      "id": "<task-id>",
      "message": {
        "role": "user",
        "parts": [
          {"type": "text", "text": "what time is it?"}
        ]
      }
    }

    Returns:
    {
      "id": "<task-id>",
      "status": "completed",
      "messages": [
        { ... original user message ... },
        {
          "role": "agent",
          "parts": [
            {"type": "text", "text": "The current time is ..."}
          ]
        }
      ]
    }
    """
    payload = request.get_json(silent=True)
    print("Received task payload:", payload)  # Debug print to inspect incoming payload
    
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400
    
    task_id = payload.get("id")
    if not task_id:
        return jsonify({"error": "Missing task ID"}), 400
    
    try:
        user_parts= payload["message"]["parts"]
        first_part = user_parts[0]
        user_text= first_part.get("text", "")
    except (KeyError, IndexError,TypeError):
        return jsonify({"error": "Invalid message format"}), 400
    
    # Generate response message with current time
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    response={
        "id": task_id,
        "status": "completed",
        "messages":[
           # Echo user message for context
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": user_text}
                ]
            },
            # Agent response with current time
            {
                "role": "agent",
                "parts": [
                    {"type": "text", "text": f"The current time is {now_str}"}
                ]
            }
        ]
    }
    return jsonify(response), 200

if __name__ == "__main__":
    print(f"Starting Tell Time Server at {BASE_URL}")
    app.run(host=HOST, port=PORT)
        