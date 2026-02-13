"""
=============================================================================
A2A Test Client - Demonstrates A2A Protocol Communication
=============================================================================

This client demonstrates how to interact with the multi-agent system using
the A2A protocol. It shows:

1. AGENT DISCOVERY: Fetching AgentCards from /.well-known/agent-card.json
2. SENDING MESSAGES: Using JSON-RPC message/send to communicate with agents
3. RESPONSE PARSING: Extracting results from JSON-RPC responses
4. DYNAMIC ROUTING: Sending requests to the Orchestrator which routes them

USAGE:
  # Make sure all agents are running first:
  # Terminal 1: python ba_agent.py     (port 5001)
  # Terminal 2: python dev_agent.py    (port 5002)
  # Terminal 3: python orchestrator.py (port 5000)
  
  # Then run this client:
  python test_client.py

WHAT HAPPENS:
  1. Client discovers the Orchestrator's AgentCard
  2. Client sends different types of requests to the Orchestrator
  3. Orchestrator's LLM decides which sub-agent to route each request to
  4. Sub-agent processes the request and returns the result
  5. Orchestrator aggregates and returns the response to the client
  6. Client displays the results

  Client --> Orchestrator --> BA Agent (for business requests)
                          |-> Dev Agent (for coding requests)
=============================================================================
"""

import asyncio
import logging
import uuid

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Client Imports
# ─────────────────────────────────────────────────────────────────────────────
# A2ACardResolver: Discovers an agent by fetching its AgentCard
# A2AClient: Sends JSON-RPC messages to an agent
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendMessageRequest,    # JSON-RPC request wrapper
    MessageSendParams,     # Parameters for message/send
    Message,               # The A2A message object
    TextPart,              # Text content within a message
    JSONRPCErrorResponse,  # Error response type
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
# Point to the Orchestrator - it will route to the right agent
ORCHESTRATOR_URL = "http://localhost:5000"

# We can also talk directly to agents (bypassing the Orchestrator)
BA_AGENT_URL = "http://localhost:5001"
DEV_AGENT_URL = "http://localhost:5002"


# =============================================================================
# Helper: Discover an Agent
# =============================================================================

async def discover_agent(base_url: str) -> dict:
    """
    Discover an agent by fetching its AgentCard.
    
    This demonstrates the A2A DISCOVERY mechanism:
    1. Send GET request to {base_url}/.well-known/agent-card.json
    2. Parse the AgentCard JSON response
    3. Read the agent's name, description, skills, and capabilities
    
    Args:
        base_url: The agent's base URL (e.g., "http://localhost:5000")
        
    Returns:
        The AgentCard object
    """
    async with httpx.AsyncClient() as client:
        resolver = A2ACardResolver(
            httpx_client=client,
            base_url=base_url,
        )
        card = await resolver.get_agent_card()
        return card


# =============================================================================
# Helper: Send a Message to an Agent
# =============================================================================

async def send_message(
    base_url: str, text: str, context_id: str = None
) -> str:
    """
    Send a message to an agent and get the response.
    
    This demonstrates the full A2A MESSAGE FLOW:
    
    1. Build a Message object with:
       - messageId: Unique ID for this message (UUID)
       - role: "user" (we're the user sending the message)
       - parts: List of content parts (TextPart for text)
       - taskId: Unique ID for this task
       - contextId: Conversation context (for multi-turn conversations)
    
    2. Wrap in MessageSendParams (the params field of JSON-RPC)
    
    3. Wrap in SendMessageRequest (the full JSON-RPC envelope):
       {
         "jsonrpc": "2.0",
         "method": "message/send",
         "id": "<request-id>",
         "params": {
           "message": { ... }
         }
       }
    
    4. Send via A2AClient.send_message()
    
    5. Parse the response (contains a Task with status and message)
    
    Args:
        base_url: The agent's base URL
        text: The text message to send
        context_id: Optional context ID for multi-turn conversations
        
    Returns:
        The agent's response text
    """
    if context_id is None:
        context_id = str(uuid.uuid4())
    
    async with httpx.AsyncClient(timeout=None) as http_client:
        # Discover the agent first (to get its card)
        resolver = A2ACardResolver(
            httpx_client=http_client,
            base_url=base_url,
        )
        agent_card = await resolver.get_agent_card()
        
        # Create the A2A client
        a2a_client = A2AClient(
            httpx_client=http_client,
            agent_card=agent_card,
        )
        
        # Build the message
        # Do NOT set taskId for new requests — let the server create the task.
        # taskId is only needed when continuing an existing task (multi-turn).
        message = Message(
            messageId=str(uuid.uuid4()),
            role="user",
            parts=[TextPart(text=text)],
            contextId=context_id,
        )
        
        # Build the JSON-RPC request
        request = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(message=message),
        )
        
        # Send and get response
        response = await a2a_client.send_message(request)
        
        # Extract text from response
        # SendMessageResponse is a RootModel — .root is either a
        # SendMessageSuccessResponse (.result) or JSONRPCErrorResponse (.error)
        rpc_response = response.root
        
        if isinstance(rpc_response, JSONRPCErrorResponse):
            return f"Error from agent: {rpc_response.error.message} (code: {rpc_response.error.code})"
        
        result = rpc_response.result
        
        if hasattr(result, 'status') and result.status and result.status.message:
            parts = result.status.message.parts
            text_parts = [p.root.text for p in parts if hasattr(p.root, 'text')]
            return "\n".join(text_parts)
        elif hasattr(result, 'parts'):
            text_parts = [p.root.text for p in result.parts if hasattr(p.root, 'text')]
            return "\n".join(text_parts)
        else:
            return str(result)


# =============================================================================
# Test Scenarios
# =============================================================================

async def test_discovery():
    """Test 1: Discover all agents and print their capabilities."""
    print("\n" + "=" * 70)
    print("TEST 1: Agent Discovery")
    print("=" * 70)
    
    for url, label in [
        (BA_AGENT_URL, "BA Agent"),
        (DEV_AGENT_URL, "Dev Agent"),
        (ORCHESTRATOR_URL, "Orchestrator"),
    ]:
        try:
            card = await discover_agent(url)
            print(f"\n✓ {label} ({url}):")
            print(f"  Name: {card.name}")
            print(f"  Description: {card.description}")
            print(f"  Version: {card.version}")
            print(f"  Skills:")
            for skill in card.skills:
                print(f"    - {skill.name}: {skill.description[:80]}...")
                print(f"      Tags: {', '.join(skill.tags)}")
        except Exception as e:
            print(f"\n✗ {label} ({url}): Not available - {e}")


async def test_ba_request():
    """Test 2: Send a BA request through the Orchestrator."""
    print("\n" + "=" * 70)
    print("TEST 2: Business Analysis Request (via Orchestrator)")
    print("  Expected: Orchestrator routes to BA Agent")
    print("=" * 70)
    
    request = (
        "Create user stories for a user authentication system that supports "
        "email/password login, social login (Google, GitHub), and two-factor "
        "authentication."
    )
    print(f"\nRequest: {request}")
    print("-" * 70)
    
    response = await send_message(ORCHESTRATOR_URL, request)
    print(f"\nResponse:\n{response}")


async def test_dev_request():
    """Test 3: Send a dev request through the Orchestrator."""
    print("\n" + "=" * 70)
    print("TEST 3: Development Request (via Orchestrator)")
    print("  Expected: Orchestrator routes to Dev Agent")
    print("=" * 70)
    
    request = (
        "Write a Python FastAPI endpoint for user registration that validates "
        "email format, hashes the password, and stores the user in a database."
    )
    print(f"\nRequest: {request}")
    print("-" * 70)
    
    response = await send_message(ORCHESTRATOR_URL, request)
    print(f"\nResponse:\n{response}")


async def test_combined_request():
    """Test 4: Send a request that might need both agents."""
    print("\n" + "=" * 70)
    print("TEST 4: Combined Request (via Orchestrator)")
    print("  Expected: Orchestrator may route to BOTH BA and Dev agents")
    print("=" * 70)
    
    request = (
        "I need to build a notification service. First, analyze the requirements "
        "and create user stories, then implement the core notification service "
        "in Python with support for email and push notifications."
    )
    print(f"\nRequest: {request}")
    print("-" * 70)
    
    response = await send_message(ORCHESTRATOR_URL, request)
    print(f"\nResponse:\n{response}")


async def test_direct_agent():
    """Test 5: Send a request directly to an agent (bypassing Orchestrator)."""
    print("\n" + "=" * 70)
    print("TEST 5: Direct Agent Communication (bypassing Orchestrator)")
    print("  Shows that agents work independently without the Orchestrator")
    print("=" * 70)
    
    request = "Write a simple Python function to calculate fibonacci numbers"
    print(f"\nDirect to Dev Agent: {request}")
    print("-" * 70)
    
    response = await send_message(DEV_AGENT_URL, request)
    print(f"\nResponse:\n{response}")


# =============================================================================
# Test 6: Adaptive Routing (Mid-Task Agent Discovery)
# =============================================================================

async def test_adaptive_routing():
    """
    Test 6: Demonstrate ADAPTIVE ROUTING.
    
    This test sends a request that may initially be routed to ONE agent,
    but the orchestrator's adaptive loop should discover that ANOTHER
    agent is also needed based on the first agent's response.
    
    HOW IT WORKS (A2A Protocol basis):
    1. Orchestrator receives the request
    2. LLM makes initial routing decision (e.g., BA Agent only)
    3. BA Agent responds (mentioning technical implementation needs)
    4. Orchestrator RE-EVALUATES using the same contextId (Section 3.4.1)
    5. LLM sees BA's output suggests Dev Agent is also needed
    6. Orchestrator routes to Dev Agent (adaptive discovery!)
    7. Both responses are aggregated
    
    NOTE: The LLM's behavior may vary. Sometimes it routes to both agents
    upfront; other times it discovers the need adaptively. The routing
    narrative in the response shows which path was taken.
    """
    print("\n" + "=" * 70)
    print("TEST 6: Adaptive Routing (Mid-Task Agent Discovery)")
    print("  Demonstrates the orchestrator discovering mid-task that")
    print("  another agent is needed based on the first agent's output")
    print("  A2A basis: contextId continuity, iterative message/send calls")
    print("=" * 70)
    
    # This request is phrased as a BA task, but the BA's output will
    # likely reference technical implementation details, triggering
    # the orchestrator to adaptively involve the Dev Agent.
    request = (
        "Analyze the requirements for a real-time notification service "
        "that supports email, SMS, and push notifications. Create "
        "detailed user stories covering all aspects of the system."
    )
    print(f"\nRequest: {request}")
    print("-" * 70)
    print("\nExpect: Orchestrator may initially route to BA Agent,")
    print("then adaptively discover Dev Agent is also needed...\n")
    
    response = await send_message(ORCHESTRATOR_URL, request)
    print(f"\nResponse:\n{response}")
    
    # Check if adaptive routing occurred
    if "ADAPTIVE" in response or "Routing Narrative" in response:
        print("\n" + "-" * 70)
        print("ADAPTIVE ROUTING was triggered!")
        print("The orchestrator discovered mid-task that another agent was needed.")
    elif "agent(s)" in response:
        print("\n" + "-" * 70)
        print("Multiple agents were used (may have been routed upfront).")
        print("Check the Routing Narrative above for details.")


# =============================================================================
# Interactive Mode
# =============================================================================

async def interactive_mode():
    """
    Interactive chat mode - send requests to the Orchestrator.
    
    This demonstrates a MULTI-TURN conversation using contextId.
    All messages in this session share the same contextId, which allows
    agents to potentially maintain conversation context.
    """
    print("\n" + "=" * 70)
    print("INTERACTIVE MODE")
    print("=" * 70)
    print("Type your requests and the Orchestrator will route them.")
    print("The Orchestrator will dynamically decide which agent to use.")
    print("Type 'quit' to exit.\n")
    
    # Single contextId for the entire conversation session
    # This is how A2A tracks multi-turn conversations
    context_id = str(uuid.uuid4())
    print(f"Context ID: {context_id}")
    print("(All messages in this session share this context ID)\n")
    
    while True:
        try:
            user_input = input("You: ").strip()
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("Goodbye!")
                break
            if not user_input:
                continue
            
            print("\nProcessing... (Orchestrator is routing your request)\n")
            response = await send_message(
                ORCHESTRATOR_URL, user_input, context_id
            )
            print(f"Agent Response:\n{response}\n")
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


# =============================================================================
# Main Entry Point
# =============================================================================

async def main():
    """Run all tests or interactive mode."""
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║          A2A Multi-Agent System - Test Client               ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  1. Run all tests (discovery + routing tests)               ║")
    print("║  2. Interactive mode (chat with the Orchestrator)           ║")
    print("║  3. Discovery only (see what agents are available)          ║")
    print("║  4. Adaptive routing test (mid-task agent discovery)        ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    
    choice = input("\nChoose (1/2/3/4): ").strip()
    
    if choice == "1":
        await test_discovery()
        await test_ba_request()
        await test_dev_request()
        await test_combined_request()
        await test_direct_agent()
        await test_adaptive_routing()
        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED!")
        print("=" * 70)
    elif choice == "2":
        await interactive_mode()
    elif choice == "3":
        await test_discovery()
    elif choice == "4":
        await test_adaptive_routing()
    else:
        print("Invalid choice. Running discovery test...")
        await test_discovery()


if __name__ == "__main__":
    asyncio.run(main())
