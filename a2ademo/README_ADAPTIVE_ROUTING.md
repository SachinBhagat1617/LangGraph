# Adaptive Routing in the A2A Orchestrator

## The Problem

The orchestrator initially routes a request to **Agent1** based on the user's message.  
But what if **Agent1's response reveals** that **Agent2 is also needed**?

**Example:**
> User: *"Create requirements for a notification service"*
>
> - Orchestrator routes to **BA Agent** (requirements = BA's skill)
> - BA Agent responds with user stories mentioning *REST APIs, message queues, database schemas*
> - Orchestrator realizes: **Dev Agent should also be involved!**

**How does the orchestrator detect this, stop, re-evaluate, and bring in more agents?**

---

## How It Works — Step by Step

### Overview Diagram

```
User ─── message/send ───▶ ORCHESTRATOR
                               │
                    ┌──────────┴──────────┐
                    │  Round 0: LLM       │
                    │  routes to BA Agent  │
                    └──────────┬──────────┘
                               │
            message/send ──────┘
            (contextId: ctx-123)
                               │
                    ┌──────────▼──────────┐
                    │    BA AGENT         │
                    │  Returns response   │
                    │  with TaskState:    │
                    │  "completed"        │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  RE-EVALUATION      │
                    │  LLM analyzes BA's  │
                    │  output + unused    │
                    │  agents' skills     │
                    │                     │
                    │  Decision:          │
                    │  "Dev Agent needed" │
                    └──────────┬──────────┘
                               │
            message/send ──────┘
            (contextId: ctx-123)  ◄── SAME contextId!
                               │
                    ┌──────────▼──────────┐
                    │    DEV AGENT        │
                    │  Returns response   │
                    │  with TaskState:    │
                    │  "completed"        │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  RE-EVALUATION      │
                    │  No more agents     │
                    │  needed → DONE      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  AGGREGATE          │
                    │  BA + Dev responses │
                    │  Return to user     │
                    └──────────┘
```

---

## The JSON-RPC Flow in Detail

### Step 1: User Sends Request to Orchestrator

The user (or test client) sends a standard A2A `message/send` JSON-RPC call:

```json
// CLIENT → ORCHESTRATOR (port 5000)
{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "req-001",
    "params": {
        "message": {
            "messageId": "msg-aaa",
            "role": "user",
            "parts": [
                {
                    "kind": "text",
                    "text": "Create requirements for a notification service"
                }
            ],
            "taskId": "task-user-001",
            "contextId": "ctx-123"
        }
    }
}
```

At this point, the orchestrator's `execute()` method is invoked.

---

### Step 2: Orchestrator — Initial LLM Routing (Round 0)

The orchestrator calls its LLM (`route_request()`) with a prompt like:

```
Available Agents:
  Agent 1: BA Agent (Skills: requirements, user stories)
  Agent 2: Dev Agent (Skills: code generation, tech design)

User's Request: "Create requirements for a notification service"

Which agent(s) should handle this?
```

**LLM responds:**

```json
{
    "selected_agents": ["http://localhost:5001"],
    "reasoning": "This is a requirements task, BA Agent is the best fit"
}
```

The orchestrator selects **BA Agent only** — it does NOT yet know Dev Agent will be needed.

---

### Step 3: Orchestrator → BA Agent (JSON-RPC `message/send`)

The orchestrator acts as an **A2A Client** and sends a JSON-RPC request to BA Agent:

```json
// ORCHESTRATOR → BA AGENT (port 5001)
{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "rpc-ba-001",
    "params": {
        "message": {
            "messageId": "msg-bbb",
            "role": "user",
            "parts": [
                {
                    "kind": "text",
                    "text": "Create requirements for a notification service"
                }
            ],
            "taskId": "task-ba-001",
            "contextId": "ctx-123"
        }
    }
}
```

**Key:** The `contextId: "ctx-123"` is the **same** as the user's original context.  
This is how A2A links all the calls into one logical session (Section 3.4.1).

---

### Step 4: BA Agent Responds

BA Agent processes the request and returns a JSON-RPC response with a **Task** object:

```json
// BA AGENT → ORCHESTRATOR
{
    "jsonrpc": "2.0",
    "id": "rpc-ba-001",
    "result": {
        "id": "task-ba-001",
        "contextId": "ctx-123",
        "status": {
            "state": "completed",
            "message": {
                "messageId": "msg-ccc",
                "role": "agent",
                "parts": [
                    {
                        "kind": "text",
                        "text": "## Requirements for Notification Service\n\n### User Stories\n1. As a user, I want to receive email notifications...\n2. As an admin, I want to configure notification templates...\n\n### Technical Requirements\n- REST API endpoints for sending notifications\n- Message queue (RabbitMQ/Kafka) for async processing\n- Database schema for notification templates and logs\n- WebSocket connections for real-time push notifications\n..."
                    }
                ]
            }
        }
    }
}
```

### **THIS IS WHERE THE MAGIC HAPPENS**

The orchestrator receives this response. Instead of immediately returning it to the user, it **pauses and re-evaluates**.

The orchestrator's `forward_to_agent()` extracts:
```python
{
    "agent_name": "BA Agent",
    "response_text": "## Requirements for Notification Service\n...",
    "success": True,                   # ← TaskState was "completed"
    "task_state": "completed"          # ← From result.status.state
}
```

---

### Step 5: Adaptive Re-Evaluation (The Key Step)

**The flow does NOT stop.** Instead of returning to the user, the orchestrator enters the **re-evaluation phase**.

#### What happens in code (`execute()` method, lines 838-884):

```python
# After BA Agent responds, check: are there unused agents?
remaining_agents = [
    a for a in self.discovered_agents
    if a.name not in processed_agents  # BA Agent is already processed
]
# remaining_agents = [DevAgent]  ← Dev Agent hasn't been used yet

# Ask LLM: "Based on BA's response, do we also need Dev Agent?"
needs_more, new_agents, reasoning = await re_evaluate_routing(
    self.llm, user_input, all_responses, remaining_agents
)
```

#### The LLM Re-Evaluation Prompt

The `build_re_evaluation_prompt()` function constructs this prompt:

```
You are a routing orchestrator analyzing whether additional agents are needed.

ORIGINAL USER REQUEST: "Create requirements for a notification service"

RESPONSES RECEIVED SO FAR:
  Agent: BA Agent
  Status: SUCCESS (TaskState: completed)
  Response Preview: ## Requirements for Notification Service
  ### User Stories
  1. As a user, I want to receive email notifications...
  ### Technical Requirements
  - REST API endpoints for sending notifications
  - Message queue (RabbitMQ/Kafka) for async processing
  - Database schema for notification templates and logs...

AGENTS NOT YET USED:
  - Dev Agent: Specialist in code generation... (Skills: Code Generation)

Does the work require involving any ADDITIONAL agents?
Respond with JSON: { "needs_more_agents": true/false, ... }
```

#### LLM Decision

The LLM analyzes BA Agent's response and sees:
- BA mentioned "REST API endpoints" → code is needed
- BA mentioned "Database schema" → implementation is needed
- BA mentioned "WebSocket connections" → technical design is needed
- Dev Agent has exactly these skills

```json
{
    "needs_more_agents": true,
    "selected_agents": ["http://localhost:5002"],
    "reasoning": "BA Agent's requirements reference REST APIs, database schemas, and WebSocket implementations that require the Dev Agent to design and implement."
}
```

---

### Step 6: Orchestrator → Dev Agent (Second JSON-RPC `message/send`)

Now the orchestrator makes a **second** `message/send` call — this time to Dev Agent:

```json
// ORCHESTRATOR → DEV AGENT (port 5002)
{
    "jsonrpc": "2.0",
    "method": "message/send",
    "id": "rpc-dev-001",
    "params": {
        "message": {
            "messageId": "msg-ddd",
            "role": "user",
            "parts": [
                {
                    "kind": "text",
                    "text": "Create requirements for a notification service"
                }
            ],
            "taskId": "task-dev-001",
            "contextId": "ctx-123"
        }
    }
}
```

**Critical detail:** The `contextId` is still `"ctx-123"` — the **same context** as the BA Agent call and the original user request. Per A2A Section 3.4.3:

> *"Context Inheritance: New tasks created within the same contextId can inherit context from previous interactions."*

This means all three calls (user → orchestrator, orchestrator → BA, orchestrator → Dev) are part of **one logical conversation**.

---

### Step 7: Dev Agent Responds

```json
// DEV AGENT → ORCHESTRATOR
{
    "jsonrpc": "2.0",
    "id": "rpc-dev-001",
    "result": {
        "id": "task-dev-001",
        "contextId": "ctx-123",
        "status": {
            "state": "completed",
            "message": {
                "messageId": "msg-eee",
                "role": "agent",
                "parts": [
                    {
                        "kind": "text",
                        "text": "## Technical Design & Implementation\n\n### REST API Endpoints\nPOST /api/notifications/send\nGET /api/notifications/{id}\n...\n\n### Database Schema\nCREATE TABLE notifications (\n  id UUID PRIMARY KEY,\n  ...\n);\n..."
                    }
                ]
            }
        }
    }
}
```

---

### Step 8: Second Re-Evaluation

The orchestrator re-evaluates again:

```python
remaining_agents = [a for a in discovered if a.name not in processed_agents]
# remaining_agents = []  ← All agents have been used!
# → Break out of the loop
```

Since there are no remaining unused agents (both BA and Dev have been called), the loop exits.

---

### Step 9: Aggregate and Return to User

The orchestrator combines both responses and returns to the user:

```json
// ORCHESTRATOR → CLIENT
{
    "jsonrpc": "2.0",
    "id": "req-001",
    "result": {
        "id": "task-user-001",
        "contextId": "ctx-123",
        "status": {
            "state": "completed",
            "message": {
                "messageId": "msg-fff",
                "role": "agent",
                "parts": [
                    {
                        "kind": "text",
                        "text": "📋 Orchestrator used 2 agent(s) across 2 routing round(s).\n\n🔀 Routing Narrative:\nRound 0 - Initial routing to: ['BA Agent']\n  Reasoning: Requirements task\nRound 1 - ADAPTIVE ROUTING: also involving ['Dev Agent']\n  Reasoning: BA's output references REST APIs, DB schemas\n\n══════════════════════\n✓ Response from BA Agent (state: completed):\n──────────────────────\n## Requirements for Notification Service\n...\n\n══════════════════════\n✓ Response from Dev Agent (state: completed):\n──────────────────────\n## Technical Design & Implementation\n..."
                    }
                ]
            }
        }
    }
}
```

---

## Complete JSON-RPC Call Sequence

```
                        JSON-RPC Calls Timeline
    ═══════════════════════════════════════════════════════════

    Client                Orchestrator              BA Agent        Dev Agent
      │                       │                       │               │
      │  message/send         │                       │               │
      │  taskId: task-001     │                       │               │
      │  contextId: ctx-123   │                       │               │
      │──────────────────────▶│                       │               │
      │                       │                       │               │
      │                       │  [LLM: route to BA]   │               │
      │                       │                       │               │
      │                       │  message/send         │               │
      │                       │  taskId: task-ba-001  │               │
      │                       │  contextId: ctx-123   │               │
      │                       │──────────────────────▶│               │
      │                       │                       │               │
      │                       │  JSON-RPC response    │               │
      │                       │  state: "completed"   │               │
      │                       │◀──────────────────────│               │
      │                       │                       │               │
      │                       │  [LLM: re-evaluate]   │               │
      │                       │  "BA mentioned APIs,  │               │
      │                       │   DB schemas → need   │               │
      │                       │   Dev Agent too!"     │               │
      │                       │                       │               │
      │                       │  message/send                         │
      │                       │  taskId: task-dev-001                 │
      │                       │  contextId: ctx-123   ◄── SAME ctx!  │
      │                       │─────────────────────────────────────▶│
      │                       │                                      │
      │                       │  JSON-RPC response                   │
      │                       │  state: "completed"                  │
      │                       │◀─────────────────────────────────────│
      │                       │                       │               │
      │                       │  [LLM: re-evaluate]   │               │
      │                       │  "No more agents      │               │
      │                       │   needed → done"      │               │
      │                       │                       │               │
      │  JSON-RPC response    │                       │               │
      │  state: "completed"   │                       │               │
      │  (BA + Dev combined)  │                       │               │
      │◀──────────────────────│                       │               │
      │                       │                       │               │
```

---

## A2A Protocol Sections That Enable This

### 1. Multiple `message/send` Calls (Section 3.1.1)

The A2A spec defines `message/send` as the primary JSON-RPC method for sending messages to agents. **Nothing restricts the orchestrator to a single call.** The orchestrator can make as many `message/send` calls as needed — first to BA Agent, then to Dev Agent.

### 2. `contextId` Groups Related Work (Section 3.4.1)

> *"The contextId logically groups multiple Tasks and Messages into a coherent session."*

All calls use `contextId: "ctx-123"`:
- User → Orchestrator: `contextId: "ctx-123"`
- Orchestrator → BA Agent: `contextId: "ctx-123"`
- Orchestrator → Dev Agent: `contextId: "ctx-123"`

This tells the A2A runtime that these are all part of **one conversation**.

### 3. Context Inheritance (Section 3.4.3)

> *"Context Inheritance: New tasks created within the same contextId can inherit context from previous interactions."*

When Dev Agent receives its task with `contextId: "ctx-123"`, it could (if implemented) access the conversation history from BA Agent's interaction in the same context.

### 4. TaskState Guides Decisions (Section 4.1.3)

The `TaskState` in each agent's response tells the orchestrator what happened:

| TaskState    | Meaning                          | Orchestrator Action           |
|-------------|----------------------------------|-------------------------------|
| `completed` | Agent finished successfully      | Re-evaluate for more agents   |
| `failed`    | Agent encountered an error       | Re-evaluate (maybe try another agent) |
| `working`   | Agent is still processing        | Wait or poll                  |
| `input-required` | Agent needs clarification   | Could route to another agent to provide input |

### 5. Orchestrator as Both Server AND Client

The A2A protocol allows any agent to also be a client:

```
┌───────────────────────────────────────┐
│           ORCHESTRATOR                │
│                                       │
│   A2A SERVER side:                    │
│     ← Receives message/send from     │
│       the user/client                 │
│                                       │
│   A2A CLIENT side:                    │
│     → Sends message/send to          │
│       BA Agent, Dev Agent, etc.       │
│                                       │
│   BRAIN (LLM):                        │
│     → Decides routing                 │
│     → Re-evaluates after responses    │
│     → Decides if more agents needed   │
└───────────────────────────────────────┘
```

---

## The Adaptive Loop in Code

Here is the simplified pseudocode of the `execute()` method:

```python
async def execute(self, context, event_queue):
    user_input = context.get_user_input()

    # ── ROUND 0: Initial Routing ──
    selected_agents, reasoning = await route_request(
        self.llm, user_input, self.discovered_agents
    )
    # Result: selected_agents = [BA Agent]

    all_responses = []
    processed_agents = set()
    current_round = 0

    while current_round < MAX_ADAPTIVE_ROUNDS:   # MAX = 3

        # ── Forward to selected agent(s) ──
        agents_to_call = [a for a in selected_agents if a.name not in processed_agents]
        if not agents_to_call:
            break

        for agent in agents_to_call:
            result = await forward_to_agent(agent, user_input, context.context_id)
            #                                                   ^^^^^^^^^^^^^^^^^
            #                                          Same contextId throughout!
            all_responses.append(result)
            processed_agents.add(agent.name)

        current_round += 1

        # ── RE-EVALUATE: Do we need more agents? ──
        remaining = [a for a in discovered if a.name not in processed_agents]
        if not remaining:
            break  # All agents have been used

        needs_more, new_agents, reasoning = await re_evaluate_routing(
            self.llm, user_input, all_responses, remaining
        )
        #           ^^^^^^^^^    ^^^^^^^^^^^^^   ^^^^^^^^^
        #           Original     What agents     Agents we
        #           request      said so far     haven't tried

        if not needs_more:
            break  # LLM says we're done

        # ── ADAPTIVE: Add new agents to the next round ──
        selected_agents = new_agents
        # Loop continues → forwards to new_agents in the next iteration

    # ── Aggregate all responses ──
    return combine(all_responses)
```

---

## When Does Re-Routing Happen vs. Not?

### Scenario 1: No Re-Routing Needed

```
User: "Write a Python function to sort a list"
  │
  ├── Round 0: LLM routes to Dev Agent (coding task)
  │   └── Dev Agent responds with Python code
  │
  ├── Re-evaluate: BA Agent's skills (requirements, user stories)
  │   └── LLM: "No, a sorting function doesn't need requirements analysis"
  │   └── needs_more_agents = false
  │
  └── Return Dev Agent's response directly
```

**Total JSON-RPC message/send calls: 1** (Orchestrator → Dev Agent)

### Scenario 2: Adaptive Re-Routing Triggered

```
User: "Create requirements for a notification service"
  │
  ├── Round 0: LLM routes to BA Agent (requirements task)
  │   └── BA Agent responds (mentions REST APIs, DB schemas, etc.)
  │
  ├── Round 1 Re-evaluate: Dev Agent's skills (code generation, tech design)
  │   └── LLM: "Yes! BA's output mentions things Dev Agent should implement"
  │   └── needs_more_agents = true, selected = [Dev Agent]
  │   └── Orchestrator → Dev Agent via message/send (same contextId)
  │   └── Dev Agent responds with technical implementation
  │
  ├── Round 2 Re-evaluate: No remaining agents
  │   └── All agents used → break
  │
  └── Return BOTH responses (BA + Dev) aggregated
```

**Total JSON-RPC message/send calls: 2** (Orchestrator → BA, Orchestrator → Dev)

### Scenario 3: Both Agents Selected Upfront (No Adaptive Needed)

```
User: "Create requirements AND implement a login feature"
  │
  ├── Round 0: LLM routes to BOTH BA Agent and Dev Agent
  │   (The word "AND" + mixed skills make it obvious)
  │   └── Orchestrator → BA Agent via message/send
  │   └── Orchestrator → Dev Agent via message/send
  │
  ├── Re-evaluate: No remaining agents
  │   └── All agents used → break
  │
  └── Return BOTH responses aggregated
```

**Total JSON-RPC message/send calls: 2** (but both in Round 0, not adaptive)

---

## Safety: MAX_ADAPTIVE_ROUNDS

To prevent infinite loops (LLM keeps thinking it needs more agents), the loop is capped at `MAX_ADAPTIVE_ROUNDS = 3`:

```python
while current_round < MAX_ADAPTIVE_ROUNDS:  # Max 3 rounds
    # ... forward, re-evaluate, maybe add more agents ...
```

With 2 agents (BA + Dev), the loop will naturally exit after at most 2 rounds (once all agents are used). With more agents registered, the cap of 3 ensures the system doesn't spiral.

---

## How to Test Adaptive Routing

```bash
# Terminal 1: Start BA Agent
python ba_agent.py

# Terminal 2: Start Dev Agent
python dev_agent.py

# Terminal 3: Start Orchestrator
python orchestrator.py

# Terminal 4: Run the adaptive routing test
python test_client.py
# Choose option 4: "Adaptive routing test"
```

Watch the **orchestrator's terminal logs** — you'll see:

```
INFO: [Round 0] Selected: ['BA Agent']
INFO: [Round 0] Delegating to: BA Agent
INFO: Forwarding to BA Agent at http://localhost:5001
INFO: Response from BA Agent: state=completed, success=True, length=1523
INFO: [Round 1] Re-evaluating: do we also need ['Dev Agent']?
INFO: Re-evaluation LLM response: {"needs_more_agents": true, ...}
INFO: [Round 1] ADAPTIVE: also involving ['Dev Agent']
INFO: [Round 1] Delegating to: Dev Agent
INFO: Forwarding to Dev Agent at http://localhost:5002
INFO: Response from Dev Agent: state=completed, success=True, length=2104
INFO: [Round 2] All available agents have been used
```

---

## Summary

| Step | What Happens | A2A Protocol Element |
|------|-------------|---------------------|
| 1 | User sends request to Orchestrator | `message/send` (JSON-RPC) |
| 2 | LLM picks Agent1 | Orchestrator's internal logic |
| 3 | Orchestrator forwards to Agent1 | `message/send` with `contextId` |
| 4 | Agent1 responds | JSON-RPC response with `TaskState` |
| 5 | **Orchestrator re-evaluates** | LLM analyzes response + unused agents |
| 6 | LLM says "need Agent2 also" | Orchestrator's adaptive logic |
| 7 | Orchestrator forwards to Agent2 | `message/send` with **same** `contextId` |
| 8 | Agent2 responds | JSON-RPC response with `TaskState` |
| 9 | Orchestrator aggregates both | Returns combined result to user |

The key insight: **the A2A protocol doesn't have a special "re-route" method.** Instead, adaptive routing is achieved by making **multiple sequential `message/send` calls** within the **same `contextId`**, with the orchestrator's LLM deciding when more agents are needed based on previous responses.
