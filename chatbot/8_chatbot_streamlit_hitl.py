import streamlit as st
from chatbot_mcp_rag_hitl import chatbot, retrieve_allThreads, get_thread_title, save_thread_title,llm_Summarizer,submit_async_task,run_async,ingest_pdf
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.types import interrupt,Command
import uuid, queue

    

# ===================== Utilities ===================== #

def generate_thread_id():
    return str(uuid.uuid4())

def get_config(thread_id: str):
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "thread_id": thread_id
        },
        "run_name": "chat_turn",
    }

def reset_chat():
    thread_id = generate_thread_id()
    st.session_state["chatThreads"].append({
        "threadId": thread_id,
        "title": "New Chat"
    })
    st.session_state["activeThreadId"] = thread_id
    st.session_state["messageHistory"] = []

def load_conversation(thread_id):
    """Load the conversation history for a given thread ID."""
    state = chatbot.get_state(config=get_config(thread_id))
    return state.values.get("messages", [])

def update_thread_title(thread_id, title):
    for thread in st.session_state["chatThreads"]:
        if thread["threadId"] == thread_id:
            thread["title"] = title
            break


# ===================== Session Init ===================== #

if "chatThreads" not in st.session_state:
    st.session_state["chatThreads"] = []
    allThreads=run_async(retrieve_allThreads()) # <-- waits here for the async function to complete
    if allThreads:
        for tid in allThreads:
            st.session_state["chatThreads"].append({
                "threadId":tid,
                "title": run_async(get_thread_title(tid))
            })
        st.session_state["activeThreadId"] = allThreads[0]
        
        # Load conversation history for the active thread
        msgs = load_conversation(allThreads[0])
        history = []
        for msg in msgs:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            history.append({"role": role, "content": msg.content})
        st.session_state["messageHistory"] = history
    else:
        initial_thread_id = generate_thread_id()
        st.session_state["chatThreads"] = [{
            "threadId": initial_thread_id,
            "title": "New Chat"
        }]
        st.session_state["activeThreadId"] = initial_thread_id
        st.session_state["messageHistory"] = []
    

if "messageHistory" not in st.session_state:
    st.session_state["messageHistory"] = []
    
if "ingested_docs" not in st.session_state:
    st.session_state["ingested_docs"] = {}
    
if "pending_interrupt" not in st.session_state:
    st.session_state["pending_interrupt"] = None
    
if "interrupt_thread_id" not in st.session_state:
    st.session_state["interrupt_thread_id"] = None
    
thread_key=str(st.session_state["activeThreadId"])
thread_docs=st.session_state["ingested_docs"]


    



# ===================== Sidebar ===================== #

st.sidebar.title("💬 Chats")

if st.sidebar.button("➕ New Chat"):
    reset_chat()
    st.rerun()

if thread_docs:
    latest_doc=list(thread_docs.values())[-1]
    st.sidebar.success(
        f"Using `{latest_doc.get('filename')}` "
        f"({latest_doc.get('chunks')} chunks from {latest_doc.get('documents')} pages)"
    )
else:
    st.sidebar.info("No PDF indexed yet.")
    
uploaded_pdf = st.sidebar.file_uploader("Upload a PDF for this chat", type=["pdf"])
if uploaded_pdf:
    if uploaded_pdf.name in thread_docs:
        st.sidebar.info(f"`{uploaded_pdf.name}` already processed for this chat.")
    else:
        with st.sidebar.status("Indexing PDF...",expanded=True) as status_box:
            summary=ingest_pdf(
                uploaded_pdf.getvalue(),
                thread_id=thread_key,
                filename=uploaded_pdf.name
            )
            thread_docs[uploaded_pdf.name] = summary
            status_box.update(label="✅ PDF indexed", state="complete", expanded=False)

st.sidebar.divider()
st.sidebar.subheader("Past conversations")
for thread in reversed(st.session_state["chatThreads"]):
    tid = thread["threadId"]
    title = thread.get("title", "New Chat") # Fallback title if missing to New Chat

    if st.sidebar.button(title, key=tid):
        st.session_state["activeThreadId"] = tid
        st.session_state["messageHistory"] = []

        msgs = load_conversation(tid)
        history = []
        for msg in msgs:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            history.append({"role": role, "content": msg.content})

        st.session_state["messageHistory"] = history


# ===================== Chat UI ===================== #

# ===================== HITL Interrupt Handling ===================== #
# Check if there's a pending interrupt to display
if st.session_state.get("pending_interrupt"):
    st.warning("🤖 The assistant needs your approval to proceed:")
    st.info(st.session_state["pending_interrupt"])
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Approve", key="approve_btn"):
            # Resume with "yes"
            thread_id = st.session_state["interrupt_thread_id"]
            with st.spinner("Processing your approval..."):
                result = run_async(
                    chatbot.ainvoke(
                        Command(resume="yes"),
                        config=get_config(thread_id)
                    )
                )
                
                # Get the latest message
                messages = result["messages"]
                last_msg = messages[-1]
                
                st.session_state["messageHistory"].append({
                    "role": "assistant",
                    "content": last_msg.content
                })
                
                # Clear interrupt state
                st.session_state["pending_interrupt"] = None
                st.session_state["interrupt_thread_id"] = None
                st.rerun()
    
    with col2:
        if st.button("❌ Reject", key="reject_btn"):
            # Resume with "no"
            thread_id = st.session_state["interrupt_thread_id"]
            with st.spinner("Processing your rejection..."):
                result = run_async(
                    chatbot.ainvoke(
                        Command(resume="no"),
                        config=get_config(thread_id)
                    )
                )
                
                # Get the latest message
                messages = result["messages"]
                last_msg = messages[-1]
                
                st.session_state["messageHistory"].append({
                    "role": "assistant",
                    "content": last_msg.content
                })
                
                # Clear interrupt state
                st.session_state["pending_interrupt"] = None
                st.session_state["interrupt_thread_id"] = None
                st.rerun()

# Main chat area To display messages and input
for msg in st.session_state["messageHistory"]:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

user_input = st.chat_input("Type here", disabled=st.session_state.get("pending_interrupt") is not None)

if user_input:
    active_thread = st.session_state.get("activeThreadId")
    
    # If no active thread, create one
    if not active_thread:
        active_thread = generate_thread_id()
        st.session_state["activeThreadId"] = active_thread
        st.session_state["chatThreads"].append({
            "threadId": active_thread,
            "title": "New Chat"
        })

    # ---- User message ----
    st.session_state["messageHistory"].append({
        "role": "user",
        "content": user_input
    })

    with st.chat_message("user"):
        st.write(user_input)
        

    with st.chat_message("assistant"):
        status_holder = {"box": None}
        ai_message_parts = []
        event_queue = queue.Queue()
        
        async def run_stream():
            """Stream messages and check for interrupts at the end"""
            try:
                async for chunk, metadata in chatbot.astream(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=get_config(active_thread),
                    stream_mode="messages"
                ):
                    event_queue.put(("chunk", chunk, metadata))
                
            except Exception as e:
                event_queue.put(("error", e))
            finally:
                # Always check state for interrupts after streaming (or interruption)
                try:
                    state = await chatbot.aget_state(config=get_config(active_thread))
                    event_queue.put(("check_interrupt", state))
                except Exception as e:
                    event_queue.put(("error", f"Failed to get state: {e}"))
                finally:
                    event_queue.put(None)
        
        # Submit async streaming task
        submit_async_task(run_stream())
        
        # Process events synchronously for Streamlit
        message_placeholder = st.empty()
        
        while True:
            item = event_queue.get()
            
            if item is None:
                break
                
            if isinstance(item, tuple) and item[0] == "error":
                st.error(f"❌ Error: {item[1]}")
                break
            
            if isinstance(item, tuple) and item[0] == "check_interrupt":
                _, state = item
                
                # Check for HITL interrupt - check both next and values
                # When interrupted, state.next will be empty and there may be __interrupt__ in values
                has_interrupt = (
                    (hasattr(state, 'next') and len(state.next) == 0 and state.values.get("__interrupt__")) or
                    state.values.get("__interrupt__", [])
                )
                
                if has_interrupt:
                    interrupts = state.values.get("__interrupt__", [])
                    if interrupts:
                        # Store the interrupt prompt and thread ID
                        prompt_to_human = interrupts[0].value
                        st.session_state["pending_interrupt"] = prompt_to_human
                        st.session_state["interrupt_thread_id"] = active_thread
                        
                        if status_holder["box"]:
                            status_holder["box"].update(
                                label="⏸️ Waiting for approval",
                                state="running",
                                expanded=False
                            )
                        st.rerun()
                else:
                    # No interrupt - finalize the message
                    ai_message = "".join(ai_message_parts)
                    
                    if status_holder["box"]:
                        status_holder["box"].update(
                            label="✅ Done",
                            state="complete",
                            expanded=False
                        )
                    
                    st.session_state["messageHistory"].append({
                        "role": "assistant",
                        "content": ai_message
                    })
                    break
            
            if isinstance(item, tuple) and item[0] == "chunk":
                _, chunk, metadata = item
                
                if isinstance(chunk, ToolMessage):
                    tool_name = getattr(chunk, "name", "tool")
                    
                    if status_holder["box"] is None:
                        status_holder["box"] = st.status(
                            f"🔧 Using `{tool_name}` …", expanded=True
                        )
                    else:
                        status_holder["box"].update(
                            label=f"🔧 Using `{tool_name}` …",
                            state="running",
                            expanded=True,
                        )
                    continue
                
                if isinstance(chunk, AIMessage):
                    if chunk.tool_calls:
                        continue
                    if chunk.content:
                        ai_message_parts.append(chunk.content)
                        message_placeholder.write("".join(ai_message_parts))

    # ===================== AUTO TITLE (FIRST MESSAGE ONLY) ===================== #
    # Only generate title if no interrupt is pending
    if len(st.session_state["messageHistory"]) == 2 and not st.session_state.get("pending_interrupt"):
        title_prompt = (
            "Generate ONE short 3–5 word title for this chat.\n"
            "Return ONLY the title.\n\n"
            f"User message: {user_input}"
        )

        title_response = llm_Summarizer.invoke([HumanMessage(content=title_prompt)])
        title = title_response.content.strip().strip('"').strip("'")
        run_async(save_thread_title(active_thread, title))
        update_thread_title(active_thread, title)
