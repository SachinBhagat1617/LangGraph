"""
=============================================================================
Streamlit UI for A2A Multi-Agent Orchestrator with Adaptive Routing
=============================================================================

This Streamlit app provides a visual chat interface to the A2A Orchestrator.
It shows:
  - Chat-style interaction with the Orchestrator
  - Real-time agent discovery status
  - Adaptive routing visualization (which agents were involved and why)
  - Full JSON-RPC flow transparency

PREREQUISITES:
  1. Start BA Agent:      python ba_agent.py       (port 5001)
  2. Start Dev Agent:     python dev_agent.py      (port 5002)
  3. Start Orchestrator:  python orchestrator.py   (port 5000)
  4. Run this app:        streamlit run streamlit_app.py

ARCHITECTURE:
  ┌──────────────┐     JSON-RPC      ┌──────────────┐
  │  Streamlit   │  message/send     │ Orchestrator  │
  │  (this app)  │ ───────────────►  │  (port 5000)  │
  └──────────────┘                   └───────┬───────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                         ┌─────────┐   ┌─────────┐    (future)
                         │BA Agent │   │Dev Agent│
                         │  5001   │   │  5002   │
                         └─────────┘   └─────────┘
=============================================================================
"""

import asyncio
import uuid
import streamlit as st
import httpx

# ─────────────────────────────────────────────────────────────────────────────
# A2A SDK Imports
# ─────────────────────────────────────────────────────────────────────────────
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    SendMessageRequest,
    MessageSendParams,
    Message,
    TextPart,
    JSONRPCErrorResponse,
)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
ORCHESTRATOR_URL = "http://localhost:5000"
BA_AGENT_URL = "http://localhost:5001"
DEV_AGENT_URL = "http://localhost:5002"

ALL_AGENT_URLS = {
    "Orchestrator": ORCHESTRATOR_URL,
    "BA Agent": BA_AGENT_URL,
    "Dev Agent": DEV_AGENT_URL,
}


# =============================================================================
# A2A Helper Functions
# =============================================================================

async def discover_agent(base_url: str) -> dict | None:
    """
    Discover an agent by fetching its AgentCard via A2A protocol.
    Returns a dict with agent info or None if unreachable.
    """
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resolver = A2ACardResolver(httpx_client=client, base_url=base_url)
            card = await resolver.get_agent_card()
            skills = [
                {"name": s.name, "description": s.description, "tags": s.tags}
                for s in card.skills
            ]
            return {
                "name": card.name,
                "description": card.description,
                "url": card.url,
                "version": card.version,
                "skills": skills,
                "online": True,
            }
    except Exception:
        return None


async def discover_all_agents() -> list[dict]:
    """Discover all known agents and return their status."""
    results = []
    for label, url in ALL_AGENT_URLS.items():
        info = await discover_agent(url)
        if info:
            results.append(info)
        else:
            results.append({"name": label, "url": url, "online": False})
    return results


async def send_a2a_message(base_url: str, text: str, context_id: str) -> str:
    """
    Send a message to an agent via A2A JSON-RPC message/send.

    This is the same flow as test_client.py:
    1. Discover the agent's card
    2. Build Message with contextId for multi-turn
    3. Wrap in SendMessageRequest (JSON-RPC envelope)
    4. Send via A2AClient
    5. Extract response text
    """
    async with httpx.AsyncClient(timeout=None) as http_client:
        resolver = A2ACardResolver(httpx_client=http_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()

        a2a_client = A2AClient(httpx_client=http_client, agent_card=agent_card)

        task_id = str(uuid.uuid4())
        message = Message(
            messageId=str(uuid.uuid4()),
            role="user",
            parts=[TextPart(text=text)],
            contextId=context_id,
        )
        request = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(message=message),
        )

        response = await a2a_client.send_message(request)
        # SendMessageResponse is a RootModel — .root is either a
        # SendMessageSuccessResponse (.result) or JSONRPCErrorResponse (.error)
        rpc_response = response.root
        
        if isinstance(rpc_response, JSONRPCErrorResponse):
            return f"Error from agent: {rpc_response.error.message} (code: {rpc_response.error.code})"
        
        result = rpc_response.result

        # Extract text from response
        if hasattr(result, "status") and result.status and result.status.message:
            parts = result.status.message.parts
            text_parts = [p.root.text for p in parts if hasattr(p.root, "text")]
            return "\n".join(text_parts) if text_parts else "No text response"
        elif hasattr(result, "parts"):
            text_parts = [p.root.text for p in result.parts if hasattr(p.root, "text")]
            return "\n".join(text_parts) if text_parts else "No text response"
        else:
            return str(result)


def run_async(coro):
    """Run an async coroutine from synchronous Streamlit code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# =============================================================================
# Streamlit Page Config
# =============================================================================

st.set_page_config(
    page_title="A2A Multi-Agent Orchestrator",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =============================================================================
# Session State Initialization
# =============================================================================

if "messages" not in st.session_state:
    st.session_state.messages = []

if "context_id" not in st.session_state:
    st.session_state.context_id = str(uuid.uuid4())

if "target" not in st.session_state:
    st.session_state.target = "Orchestrator"

if "agent_status" not in st.session_state:
    st.session_state.agent_status = []


# =============================================================================
# Sidebar — Agent Discovery & Settings
# =============================================================================

with st.sidebar:
    st.title("🤖 A2A Control Panel")
    st.caption("Multi-Agent System with Adaptive Routing")

    # ── Discover Agents ──
    st.subheader("📡 Agent Discovery")
    if st.button("🔍 Discover Agents", use_container_width=True):
        with st.spinner("Discovering agents via A2A protocol..."):
            st.session_state.agent_status = run_async(discover_all_agents())

    if st.session_state.agent_status:
        for agent in st.session_state.agent_status:
            if agent["online"]:
                with st.expander(f"✅ {agent['name']}", expanded=False):
                    st.write(f"**URL:** `{agent['url']}`")
                    st.write(f"**Version:** {agent.get('version', 'N/A')}")
                    st.write(f"**Description:** {agent.get('description', 'N/A')}")
                    if agent.get("skills"):
                        for skill in agent["skills"]:
                            st.markdown(
                                f"- **{skill['name']}**: {skill['description'][:80]}..."
                            )
                            if skill.get("tags"):
                                st.caption(f"  Tags: {', '.join(skill['tags'])}")
            else:
                st.error(f"❌ {agent['name']} — Offline ({agent['url']})")
    else:
        st.info("Click 'Discover Agents' to check agent status")

    st.divider()

    # ── Target Selection ──
    st.subheader("🎯 Send To")
    target = st.radio(
        "Route messages to:",
        options=["Orchestrator", "BA Agent (direct)", "Dev Agent (direct)"],
        index=0,
        help=(
            "**Orchestrator** uses LLM to route + adaptive re-routing.\n\n"
            "**Direct** bypasses the orchestrator and talks to an agent directly."
        ),
    )
    st.session_state.target = target

    st.divider()

    # ── Context Management ──
    st.subheader("🔗 A2A Context")
    st.code(st.session_state.context_id, language=None)
    st.caption(
        "All messages in this session share the same contextId "
        "(A2A Section 3.4.1). This enables multi-turn conversations "
        "and adaptive routing continuity."
    )

    if st.button("🔄 New Context", use_container_width=True):
        st.session_state.context_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    st.divider()

    # ── Quick Prompts ──
    st.subheader("⚡ Quick Prompts")
    st.caption("Try these to see adaptive routing in action:")

    prompts = {
        "📋 BA Task": "Create detailed user stories for an e-commerce checkout flow",
        "💻 Dev Task": "Write a Python REST API for user authentication with JWT",
        "🔀 Adaptive": (
            "Analyze the requirements for a real-time notification service "
            "that supports email, SMS, and push notifications"
        ),
        "🔗 Combined": (
            "Create requirements AND implement a simple task management API "
            "with CRUD operations"
        ),
    }

    for label, prompt in prompts.items():
        if st.button(label, use_container_width=True, help=prompt):
            st.session_state.pending_prompt = prompt
            st.rerun()


# =============================================================================
# Main Chat Area
# =============================================================================

st.title("🤖 A2A Multi-Agent Chat")
st.caption(
    "Chat with the Orchestrator — it uses LLM-based routing and "
    "adaptive re-evaluation to dynamically involve the right agents."
)

# ── Display message history ──
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            # Parse response to highlight adaptive routing sections
            text = msg["content"]

            # Check if this response contains routing narrative
            if "Routing Narrative" in text or "ADAPTIVE ROUTING" in text:
                # Split into routing info and agent responses
                parts = text.split("═" * 60)

                # Show routing narrative in an info box
                if len(parts) > 1:
                    header = parts[0]
                    if "Routing Narrative" in header:
                        # Extract just the narrative portion
                        narrative_start = header.find("Routing Narrative")
                        if narrative_start != -1:
                            before_narrative = header[:narrative_start].strip()
                            narrative = header[narrative_start:].strip()
                            if before_narrative:
                                st.markdown(before_narrative)
                            st.info(f"🔀 {narrative}")
                    else:
                        st.markdown(header)

                    # Show each agent's response in separate containers
                    for part in parts[1:]:
                        part = part.strip()
                        if not part:
                            continue
                        # Identify agent name from the response header
                        if "Response from" in part:
                            lines = part.split("\n", 2)
                            agent_header = lines[0].strip()
                            agent_body = "\n".join(lines[1:]).strip()
                            if agent_body.startswith("─"):
                                agent_body = "\n".join(
                                    agent_body.split("\n")[1:]
                                ).strip()
                            with st.expander(f"📨 {agent_header}", expanded=True):
                                st.markdown(agent_body)
                        else:
                            st.markdown(part)
                else:
                    st.markdown(text)
            else:
                st.markdown(text)
        else:
            st.markdown(msg["content"])


# ── Handle pending quick prompt ──
pending = st.session_state.pop("pending_prompt", None)

# ── Chat input ──
user_input = st.chat_input("Ask anything — the Orchestrator will route it...")

# Use pending prompt if set, otherwise use chat input
active_input = pending or user_input

if active_input:
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": active_input})
    with st.chat_message("user"):
        st.markdown(active_input)

    # Determine target URL
    if st.session_state.target == "BA Agent (direct)":
        target_url = BA_AGENT_URL
        target_label = "BA Agent"
    elif st.session_state.target == "Dev Agent (direct)":
        target_url = DEV_AGENT_URL
        target_label = "Dev Agent"
    else:
        target_url = ORCHESTRATOR_URL
        target_label = "Orchestrator"

    # Send message via A2A protocol
    with st.chat_message("assistant"):
        with st.spinner(
            f"🔄 Sending to {target_label} via A2A `message/send`...\n"
            "The orchestrator may adaptively route to multiple agents."
        ):
            try:
                response_text = run_async(
                    send_a2a_message(
                        target_url,
                        active_input,
                        st.session_state.context_id,
                    )
                )

                # Parse and display response with adaptive routing highlighting
                if "Routing Narrative" in response_text or "ADAPTIVE ROUTING" in response_text:
                    parts = response_text.split("═" * 60)

                    if len(parts) > 1:
                        header = parts[0]
                        if "Routing Narrative" in header:
                            narrative_start = header.find("Routing Narrative")
                            if narrative_start != -1:
                                before_narrative = header[:narrative_start].strip()
                                narrative = header[narrative_start:].strip()
                                if before_narrative:
                                    st.markdown(before_narrative)
                                st.info(f"🔀 {narrative}")
                        else:
                            st.markdown(header)

                        for part in parts[1:]:
                            part = part.strip()
                            if not part:
                                continue
                            if "Response from" in part:
                                lines = part.split("\n", 2)
                                agent_header = lines[0].strip()
                                agent_body = "\n".join(lines[1:]).strip()
                                if agent_body.startswith("─"):
                                    agent_body = "\n".join(
                                        agent_body.split("\n")[1:]
                                    ).strip()
                                with st.expander(
                                    f"📨 {agent_header}", expanded=True
                                ):
                                    st.markdown(agent_body)
                            else:
                                st.markdown(part)
                    else:
                        st.markdown(response_text)
                else:
                    st.markdown(response_text)

            except Exception as e:
                error_msg = str(e)
                if "Connection refused" in error_msg or "ConnectError" in error_msg:
                    st.error(
                        f"❌ Cannot connect to {target_label} at `{target_url}`.\n\n"
                        "Make sure the agents are running:\n"
                        "```\n"
                        "python ba_agent.py      # Terminal 1\n"
                        "python dev_agent.py     # Terminal 2\n"
                        "python orchestrator.py  # Terminal 3\n"
                        "```"
                    )
                    response_text = f"Error: Cannot connect to {target_label}"
                else:
                    st.error(f"❌ Error: {error_msg}")
                    response_text = f"Error: {error_msg}"

    # Save assistant response to history
    st.session_state.messages.append(
        {"role": "assistant", "content": response_text}
    )
