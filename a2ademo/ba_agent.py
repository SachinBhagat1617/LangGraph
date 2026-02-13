"""
=============================================================================
BA (Business Analyst) Agent - A2A Protocol Server
=============================================================================

This file implements a Business Analyst Agent that exposes itself as an A2A
(Agent-to-Agent) server. Other agents (like an Orchestrator) can discover
this agent via its AgentCard at /.well-known/agent-card.json and communicate
with it using JSON-RPC 2.0 over HTTP.

KEY A2A CONCEPTS DEMONSTRATED:
1. AgentCard    - Describes the agent's identity, skills, and capabilities
2. AgentSkill   - Declares what this agent can do (requirements, user stories)
3. AgentExecutor - The core logic that processes incoming messages
4. Task Lifecycle - submitted -> working -> completed
5. JSON-RPC      - The protocol used for inter-agent communication

ARCHITECTURE:
  Client/Orchestrator  --JSON-RPC-->  A2AStarletteApplication (HTTP Server)
                                          |
                                    DefaultRequestHandler (routes JSON-RPC)
                                          |
                                    BAAgentExecutor (our business logic)
                                          |
                                    ChatOCIGenAI (LLM for BA analysis)

RUNNING:
  python ba_agent.py
  Server starts on http://localhost:5001
  Agent card at http://localhost:5001/.well-known/agent-card.json
=============================================================================
"""

import asyncio
import logging
import os
import uuid

import uvicorn
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Imports
# ─────────────────────────────────────────────────────────────────────────────
# A2AStarletteApplication: Creates the ASGI app that handles HTTP requests,
#   serves the agent card at /.well-known/agent-card.json, and routes
#   JSON-RPC calls to the request handler.
from a2a.server.apps import A2AStarletteApplication

# DefaultRequestHandler: The standard JSON-RPC request handler that:
#   - Parses incoming JSON-RPC requests (message/send, tasks/get, etc.)
#   - Manages task lifecycle (create, update, retrieve tasks)
#   - Delegates actual agent logic to the AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler

# AgentExecutor: Abstract base class we must implement. It defines:
#   - execute(): Called when a message arrives for the agent to process
#   - cancel(): Called when a task cancellation is requested
# RequestContext: Holds the incoming message, task_id, context_id
from a2a.server.agent_execution import AgentExecutor, RequestContext

# EventQueue: The queue where our agent pushes events (status updates,
#   messages, artifacts) back to the framework, which then returns them
#   to the caller via JSON-RPC response or SSE stream.
from a2a.server.events import EventQueue

# InMemoryTaskStore: Simple in-memory storage for tasks. In production,
#   you'd use a database-backed TaskStore for persistence.
from a2a.server.tasks import InMemoryTaskStore

# A2A Type definitions for building the agent's identity
from a2a.types import (
    AgentCard,          # The agent's "business card" - describes who it is
    AgentSkill,         # A specific capability the agent offers
    AgentCapabilities,  # What protocol features the agent supports
)

# Utility to create a properly formatted agent text message
from a2a.utils import new_agent_text_message

# ─────────────────────────────────────────────────────────────────────────────
# LLM Import - OCI GenAI via LangChain
# ─────────────────────────────────────────────────────────────────────────────
from langchain_oci import ChatOCIGenAI

# Load environment variables from .env file (OCI credentials, model IDs, etc.)
load_dotenv()

# Configure logging so we can see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
BA_AGENT_HOST = "0.0.0.0"
BA_AGENT_PORT = 5001  # Each agent runs on a different port


# =============================================================================
# STEP 1: Define Agent Skills
# =============================================================================
# AgentSkill describes WHAT this agent can do. Other agents (like the
# Orchestrator) will read these skills from the AgentCard to decide
# whether to route a request to this agent.
#
# Fields:
#   - id: Unique identifier for the skill
#   - name: Human-readable name
#   - description: Detailed description of what the skill does
#   - tags: Keywords for discovery and matching
#   - examples: Sample queries this agent can handle
# =============================================================================

ba_skill_requirements = AgentSkill(
    id="ba-requirements-analysis",
    name="Requirements Analysis",
    description=(
        "Analyzes business requirements and creates detailed specifications. "
        "Can break down high-level ideas into structured requirements with "
        "acceptance criteria, user stories, and functional specifications."
    ),
    tags=["business-analysis", "requirements", "user-stories", "specifications"],
    examples=[
        "Analyze the requirements for a user authentication system",
        "Create user stories for an e-commerce checkout flow",
        "Write acceptance criteria for a search feature",
        "Break down the requirements for a notification system",
    ],
)

ba_skill_user_stories = AgentSkill(
    id="ba-user-story-creation",
    name="User Story Creation",
    description=(
        "Creates well-structured user stories in standard format "
        "(As a <role>, I want <action>, so that <benefit>). "
        "Includes acceptance criteria and priority recommendations."
    ),
    tags=["user-stories", "agile", "product-management", "acceptance-criteria"],
    examples=[
        "Write user stories for a login feature",
        "Create user stories for a dashboard",
        "Define acceptance criteria for a payment system",
    ],
)


# =============================================================================
# STEP 2: Define the Agent Card
# =============================================================================
# The AgentCard is the agent's "identity document" in the A2A protocol.
# It's served at /.well-known/agent-card.json and allows other agents
# to discover this agent's capabilities.
#
# Key fields:
#   - name: Agent's display name
#   - description: What this agent does
#   - url: The base URL where this agent is reachable
#   - skills: List of AgentSkill objects describing capabilities
#   - capabilities: Protocol features supported (streaming, push, etc.)
#   - defaultInputModes/defaultOutputModes: MIME types for I/O
#   - version: Agent version for compatibility tracking
# =============================================================================

ba_agent_card = AgentCard(
    name="BA Agent - Business Analyst",
    description=(
        "A Business Analyst agent that specializes in requirements analysis, "
        "user story creation, acceptance criteria definition, and functional "
        "specification writing. Routes business analysis tasks through this agent."
    ),
    url=f"http://localhost:{BA_AGENT_PORT}",
    version="1.0.0",
    skills=[ba_skill_requirements, ba_skill_user_stories],
    # Capabilities tell the caller what protocol features this agent supports
    capabilities=AgentCapabilities(
        streaming=False,           # We don't support SSE streaming (simpler)
        pushNotifications=False,   # No webhook callbacks
        stateTransitionHistory=False,  # Don't track full state history
    ),
    # This agent accepts and returns plain text
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
)


# =============================================================================
# STEP 3: Initialize the LLM
# =============================================================================
# We use OCI GenAI via LangChain. The LLM is the "brain" of our BA agent.
# It processes incoming messages and generates BA-specific responses.
# =============================================================================

def create_llm():
    """
    Creates and returns the ChatOCIGenAI LLM instance.
    
    Environment variables required:
      - OCI_SERVICE_ENDPOINT: The OCI GenAI service endpoint URL
      - OCI_COMPARTMENT_ID: Your OCI compartment OCID
      - OCI_MODEL_ID: The model to use (e.g., cohere.command-r-plus)
    """
    return ChatOCIGenAI(
        service_endpoint=os.getenv("OCI_SERVICE_ENDPOINT"),
        compartment_id=os.getenv("OCI_COMPARTMENT_ID"),
        model_id=os.getenv("OCI_MODEL_ID"),
        model_kwargs={},  # OCI model only supports default temperature
    )


# =============================================================================
# STEP 4: Implement the AgentExecutor
# =============================================================================
# This is the CORE of the agent. The AgentExecutor is an abstract class
# from the A2A SDK that we must implement. It has two methods:
#
#   execute(context, event_queue):
#     Called when a message arrives. We:
#     1. Extract the user's text from the RequestContext
#     2. Send it to our LLM with a BA-specific system prompt
#     3. Create a response message
#     4. Push a TaskStatusUpdateEvent to the EventQueue
#
#   cancel(context, event_queue):
#     Called when someone wants to cancel an ongoing task.
#     We push a canceled status update.
#
# The EventQueue is how we communicate results back to the framework.
# We push events into it, and the DefaultRequestHandler reads them
# to build the JSON-RPC response.
# =============================================================================

# Import task status types for building status update events
from a2a.types import (
    TaskState,              # Enum: submitted, working, completed, failed, etc.
    TaskStatus,             # Contains state + optional message
    TaskStatusUpdateEvent,  # The event we push to EventQueue
)


class BAAgentExecutor(AgentExecutor):
    """
    Business Analyst Agent Executor.
    
    This class implements the core logic of the BA agent. When a message
    arrives via A2A protocol, the execute() method is called with:
      - context: Contains the incoming message, task_id, context_id
      - event_queue: Where we push our response events
    
    Flow:
      1. Extract user input from context
      2. Call LLM with BA system prompt + user input
      3. Build a response message
      4. Push TaskStatusUpdateEvent with state=completed
      5. Close the event queue (signals we're done)
    """

    def __init__(self):
        """Initialize the BA agent with an LLM instance."""
        self.llm = create_llm()
        
        # The system prompt defines the BA agent's personality and expertise
        self.system_prompt = """You are an expert Business Analyst (BA) agent. 
Your role is to:
1. Analyze business requirements and break them down into clear, actionable items
2. Create well-structured user stories in the format: "As a <role>, I want <action>, so that <benefit>"
3. Define acceptance criteria for each requirement
4. Identify edge cases and non-functional requirements
5. Prioritize requirements (Must-have, Should-have, Could-have, Won't-have)

Always provide structured, professional output with clear headings and numbered items.
Be thorough but concise. Focus on clarity and completeness."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """
        Process an incoming message and generate a BA analysis.
        
        This is the main entry point called by the A2A framework when
        a message/send JSON-RPC request arrives.
        
        Args:
            context: RequestContext containing:
                - context.get_user_input(): The text from the incoming message
                - context.task_id: Unique ID for this task
                - context.context_id: Conversation context ID
                - context.message: The full Message object
            event_queue: EventQueue where we push our response events
        """
        # STEP 4a: Extract the user's message text
        user_input = context.get_user_input()
        logger.info(f"BA Agent received request: {user_input[:100]}...")
        logger.info(f"Task ID: {context.task_id}, Context ID: {context.context_id}")

        try:
            # STEP 4b: Call the LLM with our BA system prompt
            # We use LangChain's message format: system message + human message
            from langchain_core.messages import SystemMessage, HumanMessage

            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_input),
            ]

            # Invoke the LLM and get the response
            llm_response = await asyncio.to_thread(self.llm.invoke, messages)
            response_text = llm_response.content
            logger.info(f"BA Agent generated response ({len(response_text)} chars)")

            # STEP 4c: Create an agent message with the response
            # new_agent_text_message() is a utility that creates a properly
            # formatted A2A Message with role="agent"
            agent_message = new_agent_text_message(
                text=response_text,
                context_id=context.context_id,
                task_id=context.task_id,
            )

            # STEP 4d: Push a TaskStatusUpdateEvent to the event queue
            # This tells the framework: "the task is completed, here's the result"
            #
            # TaskStatusUpdateEvent fields:
            #   - taskId: Which task this update is for
            #   - contextId: The conversation context
            #   - status: TaskStatus with state and optional message
            #   - final: True means this is the last update for this task
            event = TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context.context_id,
                status=TaskStatus(
                    state=TaskState.completed,  # Task is done!
                    message=agent_message,       # Attach our response
                ),
                final=True,  # No more updates coming
            )
            await event_queue.enqueue_event(event)

        except Exception as e:
            # If something goes wrong, push a failed status
            logger.error(f"BA Agent error: {e}")
            error_message = new_agent_text_message(
                text=f"BA Agent encountered an error: {str(e)}",
                context_id=context.context_id,
                task_id=context.task_id,
            )
            error_event = TaskStatusUpdateEvent(
                taskId=context.task_id,
                contextId=context.context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=error_message,
                ),
                final=True,
            )
            await event_queue.enqueue_event(error_event)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """
        Handle a task cancellation request.
        
        When a caller sends a tasks/cancel JSON-RPC request, this method
        is called. We push a canceled status update.
        """
        logger.info(f"BA Agent: Canceling task {context.task_id}")
        cancel_event = TaskStatusUpdateEvent(
            taskId=context.task_id,
            contextId=context.context_id,
            status=TaskStatus(state=TaskState.canceled),
            final=True,
        )
        await event_queue.enqueue_event(cancel_event)


# =============================================================================
# STEP 5: Wire Everything Together and Start the Server
# =============================================================================
# The assembly follows this pattern:
#
#   1. Create AgentExecutor (our business logic)
#   2. Create TaskStore (where tasks are persisted)
#   3. Create DefaultRequestHandler (routes JSON-RPC to our executor)
#   4. Create A2AStarletteApplication (the HTTP server)
#   5. Run with uvicorn
#
# When a request arrives:
#   HTTP Request -> A2AStarletteApplication -> DefaultRequestHandler
#     -> Creates/updates Task in TaskStore
#     -> Calls AgentExecutor.execute()
#     -> Reads events from EventQueue
#     -> Returns JSON-RPC response
# =============================================================================

def main():
    """Start the BA Agent A2A server."""
    logger.info("=" * 60)
    logger.info("Starting BA (Business Analyst) Agent")
    logger.info(f"Server: http://localhost:{BA_AGENT_PORT}")
    logger.info(f"Agent Card: http://localhost:{BA_AGENT_PORT}/.well-known/agent-card.json")
    logger.info("=" * 60)

    # 1. Create our agent executor (the brain)
    ba_executor = BAAgentExecutor()

    # 2. Create an in-memory task store
    # Tasks are stored here as they progress through their lifecycle:
    #   submitted -> working -> completed/failed
    task_store = InMemoryTaskStore()

    # 3. Create the request handler
    # DefaultRequestHandler is the glue between HTTP/JSON-RPC and our executor.
    # It handles:
    #   - message/send: Creates a task, calls executor.execute()
    #   - tasks/get: Retrieves task status from the store
    #   - tasks/cancel: Calls executor.cancel()
    request_handler = DefaultRequestHandler(
        agent_executor=ba_executor,
        task_store=task_store,
    )

    # 4. Create the A2A Starlette application
    # This creates a full ASGI application that:
    #   - Serves the AgentCard at /.well-known/agent-card.json (GET)
    #   - Accepts JSON-RPC requests at / (POST)
    #   - Routes requests to our DefaultRequestHandler
    a2a_app = A2AStarletteApplication(
        agent_card=ba_agent_card,
        http_handler=request_handler,
    )

    # 5. Run with uvicorn (ASGI server)
    # a2a_app.build() returns the Starlette app ready for uvicorn
    uvicorn.run(
        a2a_app.build(),
        host=BA_AGENT_HOST,
        port=BA_AGENT_PORT,
    )


if __name__ == "__main__":
    main()
