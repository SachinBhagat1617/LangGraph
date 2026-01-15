import streamlit as st
from chatbot_mcp_rag import chatbot, retrieve_allThreads, get_thread_title, save_thread_title,llm_Summarizer,submit_async_task,run_async,ingest_pdf
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import uuid,queue

    

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

# Main chat area To display messages and input
for msg in st.session_state["messageHistory"]:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

user_input = st.chat_input("Type here")

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

        def ai_only_stream():
            event_queue: queue.Queue = queue.Queue()
            
            async def run_stream():
                try:
                    async for chunk, metadata in chatbot.astream(
                        {"messages": [HumanMessage(content=user_input)]},
                        config=get_config(active_thread),
                        stream_mode="messages"
                    ):
                        event_queue.put((chunk, metadata))
                except Exception as e:
                    event_queue.put(("error", e))
                finally:
                    event_queue.put(None)
                    
            submit_async_task(run_stream())
             # SYNC generator Streamlit consumes
            while True:
                item = event_queue.get()

                if item is None:
                    break

                if isinstance(item, tuple) and item[0] == "error":
                    yield f"❌ Error: {item[1]}"
                    break
                
                # Unpack the tuple
                if isinstance(item, tuple):
                    chunk, metadata = item
                else:
                    chunk = item
                    metadata = None

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
                    yield chunk.content
            if status_holder["box"]:
                status_holder["box"].update(
                    label="✅ Done",
                    state="complete",
                    expanded=False
                )

    ai_message = st.write_stream(ai_only_stream())


    st.session_state["messageHistory"].append({
        "role": "assistant",
        "content": ai_message
    })

    # ===================== AUTO TITLE (FIRST MESSAGE ONLY) ===================== #
    if len(st.session_state["messageHistory"]) == 2:
        title_prompt = (
            "Generate ONE short 3–5 word title for this chat.\n"
            "Return ONLY the title.\n\n"
            f"User message: {user_input}"
        )

        title_response = llm_Summarizer.invoke([HumanMessage(content=title_prompt)])
        title = title_response.content.strip().strip('"').strip("'")
        run_async(save_thread_title(active_thread, title))
        update_thread_title(active_thread, title)
