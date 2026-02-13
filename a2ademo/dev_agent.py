"""
=============================================================================
Dev (Developer) Agent - A2A Protocol Server
=============================================================================

This file implements a Developer Agent that exposes itself as an A2A server.
While the BA Agent handles requirements, this agent handles technical
implementation - code generation, technical design, and architecture.

Other agents (like the Orchestrator) discover this agent via its AgentCard
and send it work using the A2A JSON-RPC protocol.

KEY LEARNING POINTS:
- Same A2A pattern as ba_agent.py but with DIFFERENT skills and prompts
- Shows how multiple agents can coexist on different ports
- Each agent declares its own skills, so the Orchestrator can pick the right one
- The agent card URL is the key to discovery: 
    http://localhost:5002/.well-known/agent-card.json

HOW A2A ROUTING WORKS:
  1. Orchestrator fetches this agent's card from /.well-known/agent-card.json
  2. Reads the skills[] array to understand what this agent can do
  3. When a coding/technical request comes in, Orchestrator matches it
     to this agent's skills (tags, description, examples)
  4. Orchestrator sends a JSON-RPC message/send request to this agent
  5. This agent processes it and returns the result

RUNNING:
  python dev_agent.py
  Server starts on http://localhost:5002
  Agent card at http://localhost:5002/.well-known/agent-card.json
=============================================================================
"""

import asyncio
import logging
import os
import uuid

import uvicorn
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Imports (same as ba_agent.py - see that file for detailed comments)
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
)
from a2a.utils import new_agent_text_message

# LangChain OCI GenAI for LLM capabilities
from langchain_oci import ChatOCIGenAI

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
DEV_AGENT_HOST = "0.0.0.0"
DEV_AGENT_PORT = 5002  # Different port from BA agent (5001)


# =============================================================================
# STEP 1: Define Dev Agent Skills
# =============================================================================
# These skills are DIFFERENT from the BA agent. The Orchestrator uses these
# to decide: "Is this a coding task? -> Route to Dev Agent"
#
# IMPORTANT: Good skill descriptions and tags are crucial for dynamic routing.
# The Orchestrator's LLM reads these to match incoming requests to agents.
# =============================================================================

dev_skill_code_generation = AgentSkill(
    id="dev-code-generation",
    name="Code Generation",
    description=(
        "Generates clean, well-structured code based on requirements or "
        "specifications. Supports Python, JavaScript, Java, and other "
        "languages. Includes proper error handling, documentation, and "
        "follows best practices and design patterns."
    ),
    tags=["coding", "programming", "code-generation", "development", "implementation"],
    examples=[
        "Write a Python REST API for user management",
        "Implement a login authentication system",
        "Create a database schema for an e-commerce app",
        "Write unit tests for the payment service",
    ],
)

dev_skill_technical_design = AgentSkill(
    id="dev-technical-design",
    name="Technical Design & Architecture",
    description=(
        "Creates technical design documents, architecture diagrams (in text), "
        "API specifications, database schemas, and system design decisions. "
        "Evaluates trade-offs between different technical approaches."
    ),
    tags=["architecture", "design", "api-design", "database-schema", "system-design"],
    examples=[
        "Design the architecture for a microservices system",
        "Create an API specification for a REST endpoint",
        "Design a database schema for a blog platform",
        "Evaluate monolith vs microservices for our use case",
    ],
)


# =============================================================================
# STEP 2: Define the Dev Agent Card
# =============================================================================
# Notice how the skills, name, and description are all dev-focused.
# This is what makes A2A powerful - each agent advertises its capabilities
# and the orchestrator can dynamically decide who to call.
# =============================================================================

dev_agent_card = AgentCard(
    name="Dev Agent - Software Developer",
    description=(
        "A Software Developer agent that specializes in code generation, "
        "technical architecture, system design, API design, and implementation. "
        "Can write code, create technical designs, and review implementations."
    ),
    url=f"http://localhost:{DEV_AGENT_PORT}",
    version="1.0.0",
    skills=[dev_skill_code_generation, dev_skill_technical_design],
    capabilities=AgentCapabilities(
        streaming=False,
        pushNotifications=False,
        stateTransitionHistory=False,
    ),
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
)


# =============================================================================
# STEP 3: Initialize the LLM
# =============================================================================

def create_llm():
    """Creates the ChatOCIGenAI LLM for the Dev agent."""
    return ChatOCIGenAI(
        service_endpoint=os.getenv("OCI_SERVICE_ENDPOINT"),
        compartment_id=os.getenv("OCI_COMPARTMENT_ID"),
        model_id=os.getenv("OCI_MODEL_ID"),
        model_kwargs={},  # OCI model only supports default temperature
    )


# =============================================================================
# STEP 4: Implement the Dev Agent Executor
# =============================================================================
# Same interface as BAAgentExecutor, but with a DEV-focused system prompt.
# This is the beauty of A2A: same protocol, different agent personalities.
# =============================================================================

class DevAgentExecutor(AgentExecutor):
    """
    Developer Agent Executor.
    
    Processes incoming messages with a developer/engineer mindset.
    Uses a specialized system prompt that focuses on code quality,
    technical design, and best practices.
    """

    def __init__(self):
        self.llm = create_llm()
        
        # Dev-focused system prompt - different personality from BA agent
        self.system_prompt = """You are an expert Software Developer agent.
Your role is to:
1. Write clean, production-quality code with proper error handling
2. Design technical architectures and system designs
3. Create API specifications and database schemas
4. Follow SOLID principles and design patterns
5. Include code documentation and comments
6. Write code with security and performance in mind

When writing code:
- Always include imports and dependencies
- Add docstrings and inline comments
- Handle edge cases and errors gracefully
- Follow the language's conventions and best practices
- Suggest appropriate design patterns when relevant

When doing technical design:
- Consider scalability, maintainability, and performance
- Document trade-offs in your decisions
- Use clear diagrams (described in text) when helpful"""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """
        Process an incoming message and generate a technical response.
        
        Mirrors the BA agent's execute() flow but with dev-specific logic.
        See ba_agent.py for detailed comments on each step.
        """
        user_input = context.get_user_input()
        logger.info(f"Dev Agent received request: {user_input[:100]}...")
        logger.info(f"Task ID: {context.task_id}, Context ID: {context.context_id}")

        try:
            from langchain_core.messages import SystemMessage, HumanMessage

            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=user_input),
            ]

            # Call the LLM (runs in a thread since it's synchronous)
            llm_response = await asyncio.to_thread(self.llm.invoke, messages)
            response_text = llm_response.content
            logger.info(f"Dev Agent generated response ({len(response_text)} chars)")

            # Build the response message and status update event
            agent_message = new_agent_text_message(
                text=response_text,
                context_id=context.context_id,
                task_id=context.task_id,
            )

            # Push completed status with the response
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
            logger.error(f"Dev Agent error: {e}")
            error_message = new_agent_text_message(
                text=f"Dev Agent encountered an error: {str(e)}",
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
        """Handle task cancellation."""
        logger.info(f"Dev Agent: Canceling task {context.task_id}")
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

def main():
    """Start the Dev Agent A2A server."""
    logger.info("=" * 60)
    logger.info("Starting Dev (Developer) Agent")
    logger.info(f"Server: http://localhost:{DEV_AGENT_PORT}")
    logger.info(f"Agent Card: http://localhost:{DEV_AGENT_PORT}/.well-known/agent-card.json")
    logger.info("=" * 60)

    # Same assembly pattern as BA agent:
    # Executor -> TaskStore -> RequestHandler -> A2AApp -> uvicorn
    dev_executor = DevAgentExecutor()
    task_store = InMemoryTaskStore()
    
    request_handler = DefaultRequestHandler(
        agent_executor=dev_executor,
        task_store=task_store,
    )

    a2a_app = A2AStarletteApplication(
        agent_card=dev_agent_card,
        http_handler=request_handler,
    )

    uvicorn.run(
        a2a_app.build(),
        host=DEV_AGENT_HOST,
        port=DEV_AGENT_PORT,
    )


if __name__ == "__main__":
    main()
