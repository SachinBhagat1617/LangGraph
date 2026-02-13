# A2A Multi-Agent Demo

## Overview

This demo implements a **multi-agent system** using Google's [A2A (Agent-to-Agent) Protocol](https://a2a-protocol.org/). Three agents communicate using **JSON-RPC 2.0** over HTTP:

```
                    ┌─────────────────┐
                    │   Test Client    │
                    └────────┬────────┘
                             │  JSON-RPC (message/send)
                             ▼
                    ┌─────────────────┐
                    │  Orchestrator   │  (port 5000)
                    │  - Discovers    │
                    │  - Routes       │
                    │  - Aggregates   │
                    └───┬─────────┬───┘
                        │         │
           JSON-RPC     │         │     JSON-RPC
                        ▼         ▼
              ┌──────────┐  ┌──────────┐
              │ BA Agent  │  │ Dev Agent│
              │ port 5001 │  │ port 5002│
              └──────────┘  └──────────┘
```

## Agents

| Agent | Port | Specialization |
|-------|------|---------------|
| **Orchestrator** | 5000 | Discovers agents, routes requests using LLM |
| **BA Agent** | 5001 | Requirements analysis, user stories, acceptance criteria |
| **Dev Agent** | 5002 | Code generation, technical design, architecture |

## A2A Concepts Demonstrated

| Concept | What It Does | Where |
|---------|-------------|-------|
| **AgentCard** | Agent's identity & capabilities | `/.well-known/agent-card.json` |
| **AgentSkill** | Specific capabilities an agent offers | Defined in each agent |
| **JSON-RPC 2.0** | Communication protocol | `message/send` method |
| **contextId** | Links messages in a conversation | Passed through all messages |
| **taskId** | Identifies a unit of work | Created per request |
| **Task Lifecycle** | submitted → working → completed | Managed by `DefaultRequestHandler` |
| **Agent Discovery** | Finding agents at runtime | `A2ACardResolver` |

## Setup

### 1. Environment Variables

Create a `.env` file in the `a2ademo/` folder (or project root):

```env
OCI_SERVICE_ENDPOINT=https://inference.generativeai.us-chicago-1.oci.oraclecloud.com
OCI_COMPARTMENT_ID=your-compartment-ocid
OCI_MODEL_ID=your-model-id
```

### 2. Install Dependencies

```bash
pip install a2a-sdk==0.3.0 langchain-oci uvicorn python-dotenv langchain-core
```

### 3. Run the Agents

Open **three separate terminals** and run:

```bash
# Terminal 1 - BA Agent
cd a2ademo
python ba_agent.py

# Terminal 2 - Dev Agent
cd a2ademo
python dev_agent.py

# Terminal 3 - Orchestrator (start AFTER agents are running)
cd a2ademo
python orchestrator.py
```

### 4. Test

```bash
# Terminal 4 - Test Client
cd a2ademo
python test_client.py
```

## How Dynamic Routing Works

1. **Orchestrator starts** → Fetches AgentCards from BA (5001) and Dev (5002) agents
2. **Request arrives** → Orchestrator's LLM reads the request + all agent skills
3. **LLM decides** → Returns which agent(s) should handle it (as JSON)
4. **Orchestrator forwards** → Sends JSON-RPC `message/send` to the chosen agent
5. **Agent responds** → Returns a completed Task with the result
6. **Orchestrator aggregates** → Combines responses and returns to client

### Example Routing

| Request | Routed To |
|---------|-----------|
| "Create user stories for login" | BA Agent |
| "Write a Python REST API" | Dev Agent |
| "Analyze requirements and implement" | Both agents |

## File Structure

```
a2ademo/
├── ba_agent.py        # BA Agent server (port 5001)
├── dev_agent.py       # Dev Agent server (port 5002)
├── orchestrator.py    # Orchestrator server (port 5000)
├── test_client.py     # Test client with multiple test scenarios
└── README.md          # This file
```

## Key Code Patterns

### Creating an A2A Agent Server (5 steps)

```python
# 1. Define Skills
skill = AgentSkill(id="...", name="...", description="...", tags=[...])

# 2. Define Agent Card
card = AgentCard(name="...", url="...", skills=[skill], capabilities=..., ...)

# 3. Implement AgentExecutor
class MyExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        user_input = context.get_user_input()
        # ... process with LLM ...
        event = TaskStatusUpdateEvent(
            taskId=context.task_id,
            contextId=context.context_id,
            status=TaskStatus(state=TaskState.completed, message=response),
            final=True,
        )
        await event_queue.enqueue_event(event)

# 4. Wire together
handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
app = A2AStarletteApplication(agent_card=card, http_handler=handler)

# 5. Run
uvicorn.run(app.build(), host="0.0.0.0", port=5001)
```

### Sending an A2A Message (Client Side)

```python
# Discover agent
resolver = A2ACardResolver(httpx_client=client, base_url="http://localhost:5001")
card = await resolver.get_agent_card()

# Send message
a2a_client = A2AClient(httpx_client=client, agent_card=card)
request = SendMessageRequest(
    id=str(uuid.uuid4()),
    params=MessageSendParams(
        message=Message(
            messageId=str(uuid.uuid4()),
            role="user",
            parts=[TextPart(text="Your request here")],
            taskId=str(uuid.uuid4()),
            contextId=str(uuid.uuid4()),
        )
    ),
)
response = await a2a_client.send_message(request)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No agents discovered" | Start BA and Dev agents before the Orchestrator |
| Connection refused | Check that agents are running on the correct ports |
| OCI auth error | Verify `.env` file has correct OCI credentials |
| Import error | Run `pip install a2a-sdk==0.3.0` |
