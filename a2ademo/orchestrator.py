"""
=============================================================================
Orchestrator Agent - Dynamic A2A Router
=============================================================================

This is the BRAIN of the multi-agent system. The Orchestrator is responsible
for:

1. DISCOVERY: On startup, it discovers other agents (BA, Dev) by fetching
   their AgentCards from their well-known URLs.

2. ROUTING: When a request comes in, it uses an LLM to analyze the request
   and match it against the discovered agents' skills to decide which
   agent(s) should handle it.

3. DELEGATION: It forwards the request to the chosen agent(s) using the
   A2A protocol (JSON-RPC message/send).

4. AGGREGATION: It collects responses and returns them to the caller.

This demonstrates the KEY VALUE of A2A:
  - Agents are LOOSELY COUPLED - they only know about each other via AgentCards
  - Routing is DYNAMIC - the LLM decides based on skills, not hardcoded rules
  - New agents can be added without changing the Orchestrator's code
  - Each agent is independent and can be developed/deployed separately

ARCHITECTURE:
  
  Client/User
      |
      | JSON-RPC (message/send)
      v
  ┌─────────────────────────────────────────┐
  │  ORCHESTRATOR (port 5000)                │
  │  ┌─────────────┐  ┌──────────────────┐  │
  │  │ AgentCard    │  │ OrchestratorExec │  │
  │  │ (skills:     │  │   1. Get input   │  │
  │  │  routing,    │  │   2. LLM decides │  │
  │  │  delegation) │  │   3. Forward     │  │
  │  └─────────────┘  │   4. Aggregate   │  │
  │                    └───────┬──────────┘  │
  └────────────────────────────┼─────────────┘
                               |
              ┌────────────────┼────────────────┐
              |                |                 |
              v                v                 v
        ┌──────────┐    ┌──────────┐     (future agents)
        │ BA Agent  │    │ Dev Agent│
        │ port 5001 │    │ port 5002│
        └──────────┘    └──────────┘

KEY A2A CONCEPTS:
  - A2ACardResolver: Fetches an agent's card from its well-known URL
  - A2AClient: Sends JSON-RPC messages to other agents
  - contextId: Links multiple messages in a conversation
  - taskId: Identifies a specific unit of work

RUNNING:
  1. Start BA Agent:    python ba_agent.py   (port 5001)
  2. Start Dev Agent:   python dev_agent.py  (port 5002)
  3. Start Orchestrator: python orchestrator.py (port 5000)
=============================================================================
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Optional

import httpx
import uvicorn
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Imports - Server Side (Orchestrator IS an A2A server)
# ─────────────────────────────────────────────────────────────────────────────
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    # Types needed to build outgoing A2A requests (as a CLIENT)
    SendMessageRequest,
    MessageSendParams,
    Message,
    TextPart,
    Part,
    JSONRPCErrorResponse,
)
from a2a.utils import new_agent_text_message

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Imports - Client Side (Orchestrator is ALSO an A2A client)
# ─────────────────────────────────────────────────────────────────────────────
# A2ACardResolver: Fetches an AgentCard from a remote agent's well-known URL
# A2AClient: Sends JSON-RPC requests to remote agents
from a2a.client import A2ACardResolver, A2AClient

# LangChain OCI GenAI
from langchain_oci import ChatOCIGenAI

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
ORCHESTRATOR_HOST = "0.0.0.0"
ORCHESTRATOR_PORT = 5000

# URLs of the agents this orchestrator can discover
# In production, this could come from a registry or config file
AGENT_URLS = [
    "http://localhost:5001",  # BA Agent
    "http://localhost:5002",  # Dev Agent
]

# ─────────────────────────────────────────────────────────────────────────────
# Adaptive Routing Configuration
# ─────────────────────────────────────────────────────────────────────────────
# After an agent responds, the orchestrator can RE-EVALUATE whether another
# agent is also needed. This loop has a maximum number of rounds to prevent
# infinite loops. This is supported by A2A because:
#   - The orchestrator can make MULTIPLE message/send calls (Section 3.1.1)
#   - All calls share the same contextId for continuity (Section 3.4.1)
#   - Context Inheritance lets new tasks benefit from prior results (Section 3.4.3)
# ─────────────────────────────────────────────────────────────────────────────
MAX_ADAPTIVE_ROUNDS = 3


# =============================================================================
# STEP 1: Define Orchestrator Skills & Card
# =============================================================================
# Even though the Orchestrator delegates work, it's still an A2A agent itself.
# Its "skill" is intelligent routing and coordination.
# =============================================================================

orchestrator_skill = AgentSkill(
    id="orchestrator-routing",
    name="Intelligent Task Routing & Adaptive Coordination",
    description=(
        "Dynamically routes requests to the most appropriate specialist agent "
        "based on the nature of the request. Supports ADAPTIVE ROUTING: after "
        "an agent responds, the orchestrator re-evaluates whether additional "
        "agents should be involved based on the output. Can handle business "
        "analysis, coding, and multi-agent coordination scenarios."
    ),
    tags=["orchestration", "routing", "multi-agent", "coordination", "adaptive"],
    examples=[
        "Analyze requirements and then implement the solution",
        "Create user stories for a login feature",
        "Write a Python API for the order service",
        "Design and implement a notification system",
    ],
)

orchestrator_card = AgentCard(
    name="Orchestrator Agent",
    description=(
        "The central orchestrator that intelligently routes requests to "
        "specialist agents (BA, Dev, etc.) based on the nature of each request. "
        "Supports dynamic agent discovery and skill-based routing."
    ),
    url=f"http://localhost:{ORCHESTRATOR_PORT}",
    version="1.0.0",
    skills=[orchestrator_skill],
    capabilities=AgentCapabilities(
        streaming=False,
        pushNotifications=False,
        stateTransitionHistory=False,
    ),
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
)


# =============================================================================
# STEP 2: Agent Discovery
# =============================================================================
# This function discovers agents by fetching their AgentCards.
# A2ACardResolver handles the HTTP GET to /.well-known/agent-card.json.
#
# WHY THIS MATTERS:
# - Agents are discovered at runtime, not hardcoded
# - If a new agent is added, the Orchestrator finds it automatically
# - The AgentCard contains skills, which enable intelligent routing
# =============================================================================

async def discover_agents(agent_urls: list[str]) -> list[AgentCard]:
    """
    Discover available agents by fetching their AgentCards.
    
    For each URL in agent_urls, this function:
    1. Creates an A2ACardResolver pointing to the agent's base URL
    2. Calls get_agent_card() which fetches /.well-known/agent-card.json
    3. Returns the parsed AgentCard object
    
    Args:
        agent_urls: List of base URLs where agents are running
        
    Returns:
        List of AgentCard objects for successfully discovered agents
    """
    discovered = []
    
    async with httpx.AsyncClient(timeout=None) as client:
        for url in agent_urls:
            try:
                # A2ACardResolver knows to look at /.well-known/agent-card.json
                resolver = A2ACardResolver(
                    httpx_client=client,
                    base_url=url,
                )
                # Fetch and parse the agent card
                card = await resolver.get_agent_card()
                discovered.append(card)
                logger.info(f"✓ Discovered agent: {card.name} at {url}")
                
                # Log the agent's skills for visibility
                for skill in card.skills:
                    logger.info(f"  Skill: {skill.name} - {skill.description[:60]}...")
                    
            except Exception as e:
                logger.warning(f"✗ Could not discover agent at {url}: {e}")
                logger.warning(f"  Make sure the agent is running at {url}")
    
    return discovered


# =============================================================================
# STEP 3: LLM-Based Routing Logic
# =============================================================================
# This is where the "dynamic" part happens. Instead of if/else rules,
# we use an LLM to understand the request and match it to agent skills.
# =============================================================================

def create_llm():
    """Creates the ChatOCIGenAI LLM for routing decisions."""
    return ChatOCIGenAI(
        service_endpoint=os.getenv("OCI_SERVICE_ENDPOINT"),
        compartment_id=os.getenv("OCI_COMPARTMENT_ID"),
        model_id=os.getenv("OCI_MODEL_ID"),
        model_kwargs={},  # OCI model only supports default temperature
    )


def build_routing_prompt(user_request: str, agents: list[AgentCard]) -> str:
    """
    Build a prompt that asks the LLM to choose the best agent for the request.
    
    The prompt includes:
    - The user's request
    - A list of available agents with their skills
    - Instructions to respond with a JSON decision
    
    Args:
        user_request: The incoming user message
        agents: List of discovered AgentCards with their skills
        
    Returns:
        A formatted prompt string for the routing LLM
    """
    # Build a description of each available agent and its skills
    agent_descriptions = []
    for i, agent in enumerate(agents):
        skills_text = "\n".join(
            f"    - Skill: {skill.name}\n"
            f"      Description: {skill.description}\n"
            f"      Tags: {', '.join(skill.tags)}\n"
            f"      Examples: {', '.join(skill.examples or [])}"
            for skill in agent.skills
        )
        agent_descriptions.append(
            f"  Agent {i + 1}:\n"
            f"    Name: {agent.name}\n"
            f"    Description: {agent.description}\n"
            f"    URL: {agent.url}\n"
            f"    Skills:\n{skills_text}"
        )
    
    agents_section = "\n\n".join(agent_descriptions)
    
    return f"""You are a routing agent. Your job is to analyze the user's request and decide which agent(s) should handle it.

Available Agents:
{agents_section}

User's Request: "{user_request}"

Instructions:
1. Analyze the user's request carefully
2. Match it against the available agents' skills, descriptions, and tags
3. Choose the BEST agent for this request
4. If the request clearly needs BOTH agents (e.g., "write requirements AND implement code"), select both

Respond with ONLY a JSON object in this exact format:
{{
    "selected_agents": ["<agent_url_1>", "<agent_url_2>"],
    "reasoning": "Brief explanation of why these agents were selected"
}}

IMPORTANT: 
- The "selected_agents" array must contain the exact URL(s) from the agent list above
- Select only the agents truly needed (usually just one)
- Only select multiple agents if the request explicitly needs different types of work
"""


async def route_request(
    llm, user_request: str, agents: list[AgentCard]
) -> tuple[list[AgentCard], str]:
    """
    Use the LLM to decide which agent(s) should handle the request.
    
    Args:
        llm: The ChatOCIGenAI instance
        user_request: The incoming user message
        agents: List of discovered AgentCards
        
    Returns:
        Tuple of (selected_agents, reasoning)
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    prompt = build_routing_prompt(user_request, agents)
    
    messages = [
        HumanMessage(content=prompt),
    ]
    
    response = await asyncio.to_thread(llm.invoke, messages)
    response_text = response.content.strip()
    
    logger.info(f"Routing LLM response: {response_text}")
    
    # Parse the JSON response
    try:
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        decision = json.loads(response_text)
        selected_urls = decision.get("selected_agents", [])
        reasoning = decision.get("reasoning", "No reasoning provided")
        
        # Map URLs back to AgentCard objects
        selected = [a for a in agents if a.url in selected_urls]
        
        if not selected:
            # Fallback: if LLM didn't match URLs perfectly, try partial match
            for agent in agents:
                for url in selected_urls:
                    if url in agent.url or agent.url in url:
                        selected.append(agent)
                        break
        
        if not selected:
            # Ultimate fallback: use the first agent
            logger.warning("Could not match any agent from LLM response, using first agent")
            selected = [agents[0]]
            reasoning = "Fallback: no agent matched, using first available"
        
        return selected, reasoning
        
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse routing decision: {e}")
        logger.error(f"Raw response: {response_text}")
        # Fallback: send to first agent
        return [agents[0]], f"Parsing failed, falling back to {agents[0].name}"


# =============================================================================
# STEP 3b: Adaptive Re-Evaluation (Mid-Task Agent Discovery)
# =============================================================================
# This is the KEY FEATURE for dynamic agent composition.
#
# SCENARIO:
#   1. User asks: "Create requirements for a notification service"
#   2. Orchestrator routes to BA Agent (initial decision)
#   3. BA Agent responds with detailed requirements
#   4. Orchestrator RE-EVALUATES: BA's output mentions REST APIs, message
#      queues, database schemas → Dev Agent should also be involved!
#   5. Orchestrator routes to Dev Agent with full context
#   6. Aggregates both responses
#
# A2A PROTOCOL BASIS:
#   - Multiple message/send calls share the same contextId (Section 3.4.1)
#   - "Context Inheritance: New tasks created within the same contextId can
#     inherit context from previous interactions" (Section 3.4.3)
#   - The orchestrator acts as BOTH A2A Server (to the user) and A2A Client
#     (to sub-agents), making iterative routing decisions
# =============================================================================

def build_re_evaluation_prompt(
    user_request: str,
    responses: list[dict],
    remaining_agents: list[AgentCard],
) -> str:
    """
    Build a prompt asking the LLM if MORE agents should be involved.
    
    After receiving responses from the initially-selected agents, this prompt
    asks the LLM to analyze whether the output reveals a need for agents
    that weren't part of the initial routing decision.
    
    Args:
        user_request: The original user message
        responses: List of response dicts from forward_to_agent
        remaining_agents: AgentCards for agents NOT yet used
        
    Returns:
        A formatted prompt string for the re-evaluation LLM call
    """
    # Summarize responses received so far
    response_summaries = []
    for resp in responses:
        status = "SUCCESS" if resp["success"] else "FAILED"
        # Truncate long responses to keep the prompt manageable
        text_preview = resp["response_text"][:500]
        response_summaries.append(
            f"  Agent: {resp['agent_name']}\n"
            f"  Status: {status} (TaskState: {resp['task_state']})\n"
            f"  Response Preview: {text_preview}..."
        )
    responses_section = "\n\n".join(response_summaries)
    
    # Describe remaining available agents
    remaining_descriptions = []
    for agent in remaining_agents:
        skills = ", ".join(s.name for s in agent.skills)
        remaining_descriptions.append(
            f"  - {agent.name}: {agent.description[:100]}... (Skills: {skills})"
        )
    remaining_section = (
        "\n".join(remaining_descriptions) if remaining_descriptions else "  None"
    )
    
    return f"""You are a routing orchestrator analyzing whether additional agents are needed.

You have already sent a task to some agents and received their responses.
Now decide: does the work require involving any ADDITIONAL agents?

ORIGINAL USER REQUEST: "{user_request}"

RESPONSES RECEIVED SO FAR:
{responses_section}

AGENTS NOT YET USED:
{remaining_section}

INSTRUCTIONS:
- Analyze the original request AND the responses received
- Determine if the responses FULLY satisfy the original request
- If an unused agent's skills would ADD VALUE (e.g., the BA response suggests
  implementation is needed, or the Dev response references needing requirements),
  select that additional agent
- Do NOT select additional agents unless there is CLEAR evidence they are needed

Respond with ONLY a JSON object:
{{
    "needs_more_agents": true or false,
    "selected_agents": ["<agent_url_1>"],
    "reasoning": "Why these additional agents are needed (or why not)"
}}

If no more agents are needed, set "needs_more_agents" to false and "selected_agents" to [].
"""


async def re_evaluate_routing(
    llm,
    user_request: str,
    responses: list[dict],
    remaining_agents: list[AgentCard],
) -> tuple[bool, list[AgentCard], str]:
    """
    Ask the LLM if more agents should be involved based on current responses.
    
    This implements the ADAPTIVE part of routing. After the initial agents
    complete their tasks, we check if the results indicate that another
    agent should also contribute.
    
    A2A RELEVANCE:
    - Each subsequent agent call is a standard message/send (Section 3.1.1)
    - The orchestrator maintains context via contextId (Section 3.4.1)
    - This pattern is used in Google's a2a-samples (e.g., RoutingAgent in
      a2a_multiagent_host checks responses and decides next steps)
    
    Args:
        llm: The ChatOCIGenAI instance
        user_request: Original user message
        responses: List of response dicts from agents called so far
        remaining_agents: AgentCards for agents not yet used
        
    Returns:
        Tuple of (needs_more_agents, selected_agents, reasoning)
    """
    from langchain_core.messages import HumanMessage
    
    prompt = build_re_evaluation_prompt(user_request, responses, remaining_agents)
    messages = [HumanMessage(content=prompt)]
    
    response = await asyncio.to_thread(llm.invoke, messages)
    response_text = response.content.strip()
    
    logger.info(f"Re-evaluation LLM response: {response_text}")
    
    try:
        # Extract JSON (handle markdown code blocks)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        decision = json.loads(response_text)
        needs_more = decision.get("needs_more_agents", False)
        reasoning = decision.get("reasoning", "No reasoning provided")
        
        if not needs_more:
            return False, [], reasoning
        
        # Map URLs back to AgentCard objects
        selected_urls = decision.get("selected_agents", [])
        selected = [a for a in remaining_agents if a.url in selected_urls]
        
        if not selected:
            # Try partial URL matching
            for agent in remaining_agents:
                for url in selected_urls:
                    if url in agent.url or agent.url in url:
                        selected.append(agent)
                        break
        
        return bool(selected), selected, reasoning
        
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse re-evaluation: {e}")
        return False, [], f"Re-evaluation parsing failed: {e}"


# =============================================================================
# STEP 4: A2A Client - Forward Request to Agent
# =============================================================================
# The Orchestrator acts as an A2A CLIENT when talking to BA/Dev agents.
# It builds a SendMessageRequest (JSON-RPC) and sends it using A2AClient.
#
# JSON-RPC FLOW:
#   Orchestrator                        BA/Dev Agent
#       |                                    |
#       |  POST / (JSON-RPC 2.0)             |
#       |  {                                 |
#       |    "jsonrpc": "2.0",               |
#       |    "method": "message/send",       |
#       |    "id": "<unique-id>",            |
#       |    "params": {                     |
#       |      "message": {                  |
#       |        "messageId": "...",         |
#       |        "role": "user",             |
#       |        "parts": [{"text": "..."}], |
#       |        "taskId": "...",            |
#       |        "contextId": "..."          |
#       |      }                             |
#       |    }                               |
#       |  }                                 |
#       |  ------------------------------>   |
#       |                                    |
#       |  JSON-RPC Response                 |
#       |  {                                 |
#       |    "jsonrpc": "2.0",               |
#       |    "id": "<same-id>",              |
#       |    "result": { Task object }       |
#       |  }                                 |
#       |  <------------------------------   |
# =============================================================================

async def forward_to_agent(
    agent_card: AgentCard, user_text: str, context_id: str
) -> dict:
    """
    Forward a user's request to a specific agent using A2A protocol.
    
    Returns a DICT (not just text) so the orchestrator can inspect the
    A2A TaskState and make adaptive routing decisions.
    
    Return dict:
        {
            "agent_name": str,      # Name of the agent that responded
            "response_text": str,   # The agent's response content
            "success": bool,        # True if task completed successfully
            "task_state": str,      # A2A TaskState (completed, failed, etc.)
        }
    
    A2A PROTOCOL DETAILS:
    - Uses message/send JSON-RPC method (Section 3.1.1)
    - contextId links this call to the orchestrator's session (Section 3.4.1)
    - The Task's status.state in the response tells us if the agent succeeded
    - On failure/exception, returns a dict with success=False so the
      orchestrator's adaptive loop can decide what to do next
    """
    try:
        async with httpx.AsyncClient(timeout=None) as http_client:
            # Create A2A client pointing to the target agent
            a2a_client = A2AClient(
                httpx_client=http_client,
                agent_card=agent_card,
            )
            
            # Build the A2A message
            # Do NOT set taskId — that tells the server to look up an existing
            # task. For a NEW request, omit taskId so the server creates one.
            message = Message(
                messageId=str(uuid.uuid4()),
                role="user",
                parts=[TextPart(text=user_text)],
                contextId=context_id,
            )
            
            # Wrap in JSON-RPC envelope
            request = SendMessageRequest(
                id=str(uuid.uuid4()),
                params=MessageSendParams(message=message),
            )
            
            logger.info(f"Forwarding to {agent_card.name} at {agent_card.url}")
            
            # Send the JSON-RPC request
            response = await a2a_client.send_message(request)
            # SendMessageResponse is a RootModel — .root is either a
            # SendMessageSuccessResponse (has .result) or a
            # JSONRPCErrorResponse (has .error). Check which one we got.
            rpc_response = response.root
            
            if isinstance(rpc_response, JSONRPCErrorResponse):
                # The agent returned a JSON-RPC error
                error_msg = rpc_response.error.message
                error_code = rpc_response.error.code
                logger.error(
                    f"JSON-RPC error from {agent_card.name}: "
                    f"code={error_code}, message={error_msg}"
                )
                return {
                    "agent_name": agent_card.name,
                    "response_text": f"Agent returned error: {error_msg} (code: {error_code})",
                    "success": False,
                    "task_state": "failed",
                }
            
            result = rpc_response.result
            
            # ── Extract A2A TaskState from the response ──
            # The Task's status.state tells us whether the agent completed
            # or failed. This is a core A2A concept (Section 4.1.3).
            task_state = "completed"
            if hasattr(result, 'status') and result.status and result.status.state:
                task_state = str(result.status.state)
            
            # ── Extract response text ──
            response_text = ""
            if hasattr(result, 'status') and result.status and result.status.message:
                parts = result.status.message.parts
                text_parts = [p.root.text for p in parts if hasattr(p.root, 'text')]
                response_text = "\n".join(text_parts) if text_parts else "No text response"
            elif hasattr(result, 'parts'):
                text_parts = [p.root.text for p in result.parts if hasattr(p.root, 'text')]
                response_text = "\n".join(text_parts) if text_parts else "No text response"
            else:
                response_text = str(result)
            
            success = "failed" not in task_state.lower()
            logger.info(
                f"Response from {agent_card.name}: state={task_state}, "
                f"success={success}, length={len(response_text)}"
            )
            
            return {
                "agent_name": agent_card.name,
                "response_text": response_text,
                "success": success,
                "task_state": task_state,
            }
            
    except Exception as e:
        # Network errors, timeouts, agent unavailable, etc.
        # Return a structured error dict so the adaptive loop can handle it
        logger.error(f"Error forwarding to {agent_card.name}: {e}")
        return {
            "agent_name": agent_card.name,
            "response_text": f"Error communicating with agent: {str(e)}",
            "success": False,
            "task_state": "failed",
        }


# =============================================================================
# STEP 5: Implement the Orchestrator Executor
# =============================================================================
# This is the core orchestration logic. When a request arrives:
# 1. Discover agents (if not already done)
# 2. Use LLM to route the request
# 3. Forward to the selected agent(s)
# 4. Aggregate responses
# 5. Return the combined result
# =============================================================================

class OrchestratorExecutor(AgentExecutor):
    """
    Orchestrator Agent Executor.
    
    This executor doesn't do the work itself - it DELEGATES to other agents.
    It's like a project manager who:
    - Knows what each team member (agent) is good at
    - Decides who should handle each task
    - Coordinates the work
    - Assembles the final result
    """

    def __init__(self):
        self.llm = create_llm()
        self.discovered_agents: list[AgentCard] = []
        self._discovery_done = False

    async def _ensure_agents_discovered(self):
        """
        Discover agents if not already done.
        
        This lazy discovery means:
        - Agents don't need to be running when Orchestrator starts
        - We re-discover if discovery hasn't been done yet
        - New agents can be discovered by restarting the Orchestrator
        """
        if not self._discovery_done:
            logger.info("Discovering agents...")
            self.discovered_agents = await discover_agents(AGENT_URLS)
            self._discovery_done = True
            
            if not self.discovered_agents:
                logger.error("No agents discovered! Make sure agents are running.")
            else:
                logger.info(f"Discovered {len(self.discovered_agents)} agent(s)")

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """
        Main orchestration logic with ADAPTIVE ROUTING.
        
        This uses an iterative pattern built on A2A protocol concepts:
        
        1. LLM makes initial routing decision → select agent(s)
        2. Forward to selected agent(s) via A2A message/send (JSON-RPC)
        3. ADAPTIVE STEP: Analyze agent response(s) with LLM
           → Does the output reveal we need ANOTHER agent not yet used?
        4. If yes, route to the newly-identified agent with full context
        5. Repeat until no more agents needed or MAX_ADAPTIVE_ROUNDS hit
        6. Aggregate ALL responses and return to caller
        
        A2A PROTOCOL FOUNDATIONS:
        - Each forward uses message/send (Section 3.1.1)
        - All calls share the same contextId for session continuity (Sec 3.4.1)
        - Task states (completed, failed) in responses guide re-evaluation
        - The orchestrator is simultaneously an A2A server AND client
        
        EXAMPLE ADAPTIVE FLOW:
        Suppose the user asks: "Create requirements for a notification service"
        - Round 0: LLM routes to BA Agent → requirements & user stories
        - BA Agent responds with detailed requirements
        - Round 1: LLM re-evaluates → BA mentions REST APIs, database schemas
          → LLM decides Dev Agent should also be involved
        - Dev Agent designs/implements based on BA's requirements
        - Round 2: LLM re-evaluates → all work is done, loop ends
        - Orchestrator aggregates both responses and returns them
        """
        user_input = context.get_user_input()
        logger.info(f"Orchestrator received: {user_input[:100]}...")

        try:
            # ── STEP A: Discover agents ──
            await self._ensure_agents_discovered()

            if not self.discovered_agents:
                error_msg = new_agent_text_message(
                    text="No agents are currently available. Please start the BA and Dev agents first.",
                    context_id=context.context_id,
                    task_id=context.task_id,
                )
                event = TaskStatusUpdateEvent(
                    taskId=context.task_id,
                    contextId=context.context_id,
                    status=TaskStatus(state=TaskState.failed, message=error_msg),
                    final=True,
                )
                await event_queue.enqueue_event(event)
                return

            # ── STEP B: Initial routing decision ──
            logger.info("Routing request to appropriate agent...")
            selected_agents, reasoning = await route_request(
                self.llm, user_input, self.discovered_agents
            )
            logger.info(f"[Round 0] Selected: {[a.name for a in selected_agents]}")
            logger.info(f"[Round 0] Reasoning: {reasoning}")

            # ── STEP C: ADAPTIVE ROUTING LOOP ──
            # This is the core adaptive pattern. We track:
            #   - all_responses: Results from every agent call (list of dicts)
            #   - processed_agents: Which agents we've already called (by name)
            #   - routing_log: Human-readable narrative of routing decisions
            all_responses: list[dict] = []
            processed_agents: set[str] = set()
            routing_log: list[str] = []

            routing_log.append(
                f"Round 0 - Initial routing to: {[a.name for a in selected_agents]}\n"
                f"  Reasoning: {reasoning}"
            )

            current_round = 0

            while current_round < MAX_ADAPTIVE_ROUNDS:
                # Filter out agents we've already called
                agents_to_call = [
                    a for a in selected_agents if a.name not in processed_agents
                ]

                if not agents_to_call:
                    logger.info(f"[Round {current_round}] No new agents to call")
                    break

                # ── Forward to each selected agent via A2A message/send ──
                # Each call is a standard JSON-RPC message/send request.
                # All calls share the same contextId (A2A Section 3.4.1)
                for agent_card in agents_to_call:
                    logger.info(f"[Round {current_round}] Delegating to: {agent_card.name}")
                    result = await forward_to_agent(
                        agent_card=agent_card,
                        user_text=user_input,
                        context_id=context.context_id,
                    )
                    all_responses.append(result)
                    processed_agents.add(agent_card.name)
                    logger.info(
                        f"[Round {current_round}] {agent_card.name} → "
                        f"state={result['task_state']}, success={result['success']}"
                    )

                current_round += 1

                # ── ADAPTIVE RE-EVALUATION ──
                # "While performing Agent1's task, the orchestrator sees it
                #  requires Agent3 also" - this is where that happens.
                # We ask the LLM: given the responses so far, do we need
                # ANY of the remaining agents we haven't used yet?
                remaining_agents = [
                    a for a in self.discovered_agents
                    if a.name not in processed_agents
                ]

                if not remaining_agents:
                    logger.info(f"[Round {current_round}] All available agents have been used")
                    routing_log.append(
                        f"Round {current_round} - All agents have been consulted"
                    )
                    break

                # Ask LLM: "Based on what we got, do we need more agents?"
                logger.info(
                    f"[Round {current_round}] Re-evaluating: "
                    f"do we also need {[a.name for a in remaining_agents]}?"
                )
                needs_more, new_agents, re_reasoning = await re_evaluate_routing(
                    self.llm, user_input, all_responses, remaining_agents
                )

                if not needs_more:
                    logger.info(f"[Round {current_round}] No additional agents needed")
                    routing_log.append(
                        f"Round {current_round} - Re-evaluation: no more agents needed\n"
                        f"  Reasoning: {re_reasoning}"
                    )
                    break

                # ── New agents discovered mid-task! ──
                # This is the adaptive routing in action:
                # Agent1 has finished, and its output reveals Agent3 is needed.
                logger.info(
                    f"[Round {current_round}] ADAPTIVE: also involving "
                    f"{[a.name for a in new_agents]}"
                )
                routing_log.append(
                    f"Round {current_round} - ADAPTIVE ROUTING: also involving "
                    f"{[a.name for a in new_agents]}\n"
                    f"  Reasoning: {re_reasoning}"
                )
                selected_agents = new_agents

            # ── STEP D: Aggregate ALL responses ──
            routing_narrative = "\n".join(routing_log)

            if len(all_responses) == 1:
                resp = all_responses[0]
                combined = (
                    f"📋 Response from {resp['agent_name']}:\n"
                    f"{'─' * 50}\n"
                    f"{resp['response_text']}"
                )
            else:
                # Multiple responses from adaptive routing
                combined_parts = [
                    f"📋 Orchestrator used {len(all_responses)} agent(s) "
                    f"across {current_round} routing round(s).\n"
                    f"\n🔀 Routing Narrative (Adaptive Routing Log):\n{routing_narrative}\n"
                ]
                for resp in all_responses:
                    status_icon = "✓" if resp["success"] else "✗"
                    combined_parts.append(
                        f"\n{'═' * 60}\n"
                        f"{status_icon} Response from {resp['agent_name']} "
                        f"(state: {resp['task_state']}):\n"
                        f"{'─' * 60}\n"
                        f"{resp['response_text']}\n"
                    )
                combined = "\n".join(combined_parts)

            # ── STEP E: Return the aggregated result ──
            agent_message = new_agent_text_message(
                text=combined,
                context_id=context.context_id,
                task_id=context.task_id,
            )

            event = TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context.context_id,
                status=TaskStatus(
                    state=TaskState.completed,
                    message=agent_message,
                ),
                final=True,
            )
            await event_queue.enqueue_event(event)

        except Exception as e:
            logger.error(f"Orchestrator error: {e}", exc_info=True)
            error_message = new_agent_text_message(
                text=f"Orchestrator error: {str(e)}",
                context_id=context.context_id,
                task_id=context.task_id,
            )
            error_event = TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context.context_id,
                status=TaskStatus(state=TaskState.failed, message=error_message),
                final=True,
            )
            await event_queue.enqueue_event(error_event)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Handle task cancellation."""
        logger.info(f"Orchestrator: Canceling task {context.task_id}")
        cancel_event = TaskStatusUpdateEvent(
            taskId=context.task_id,
            contextId=context.context_id,
            status=TaskStatus(state=TaskState.canceled),
            final=True,
        )
        await event_queue.enqueue_event(cancel_event)


# =============================================================================
# STEP 6: Wire Everything Together
# =============================================================================

def main():
    """Start the Orchestrator A2A server."""
    logger.info("=" * 60)
    logger.info("Starting Orchestrator Agent")
    logger.info(f"Server: http://localhost:{ORCHESTRATOR_PORT}")
    logger.info(f"Agent Card: http://localhost:{ORCHESTRATOR_PORT}/.well-known/agent-card.json")
    logger.info("=" * 60)
    logger.info(f"Will discover agents at: {AGENT_URLS}")
    logger.info("Make sure BA Agent (5001) and Dev Agent (5002) are running!")
    logger.info("=" * 60)

    orchestrator_executor = OrchestratorExecutor()
    task_store = InMemoryTaskStore()

    request_handler = DefaultRequestHandler(
        agent_executor=orchestrator_executor,
        task_store=task_store,
    )

    a2a_app = A2AStarletteApplication(
        agent_card=orchestrator_card,
        http_handler=request_handler,
    )

    uvicorn.run(
        a2a_app.build(),
        host=ORCHESTRATOR_HOST,
        port=ORCHESTRATOR_PORT,
    )


if __name__ == "__main__":
    main()
