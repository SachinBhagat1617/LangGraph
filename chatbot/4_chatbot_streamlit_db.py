import streamlit as st
from chatbot_backend_db import chatbot, retrieve_allThreads, get_thread_title, save_thread_title,llm_Summarizer
from langchain_core.messages import HumanMessage, AIMessage
import uuid

# ===================== Utilities ===================== #

def generate_thread_id():
    return str(uuid.uuid4())

def get_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id}}

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
    allThreads=retrieve_allThreads()
    if allThreads:
        for tid in allThreads:
            st.session_state["chatThreads"].append({
                "threadId":tid,
                "title": get_thread_title(tid)
            })
        st.session_state["activeThreadId"] = allThreads[0]
    else:
        initial_thread_id = generate_thread_id()
        st.session_state["chatThreads"] = [{
            "threadId": initial_thread_id,
            "title": "New Chat"
        }]
        st.session_state["activeThreadId"] = initial_thread_id
    

if "messageHistory" not in st.session_state:
    st.session_state["messageHistory"] = []


# ===================== Sidebar ===================== #

st.sidebar.title("💬 Chats")

if st.sidebar.button("➕ New Chat"):
    reset_chat()

st.sidebar.divider()

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
    active_thread = st.session_state["activeThreadId"]

    # ---- User message ----
    st.session_state["messageHistory"].append({
        "role": "user",
        "content": user_input
    })

    with st.chat_message("user"):
        st.write(user_input)

    # ---- Assistant streaming ----
    with st.chat_message("assistant"):
        def ai_only_stream():
            for chunk, _ in chatbot.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=get_config(active_thread),
                stream_mode="messages"
            ):
                if isinstance(chunk, AIMessage):
                    yield chunk.content

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

        # Separate thread for NO memory pollution
        title_thread_id = f"title-{uuid.uuid4()}"

        title_response = llm_Summarizer.invoke([HumanMessage(content=title_prompt)])
        title = title_response.content.strip().strip('"').strip("'")
        save_thread_title(active_thread, title)
        update_thread_title(active_thread, title)
