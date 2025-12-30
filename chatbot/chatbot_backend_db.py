from langgraph.graph import StateGraph, START, END
from typing import TypedDict,Annotated
from langchain_core.messages import BaseMessage,HumanMessage,AIMessage
from langchain_oci import ChatOCIGenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
import os,sqlite3

load_dotenv()

service_endpoint= os.getenv("OCI_SERVICE_ENDPOINT")
compartment_id= os.getenv("OCI_COMPARTMENT_ID")
model_id= os.getenv("OCI_MODEL_ID")
model= ChatOCIGenAI(
    service_endpoint=service_endpoint,
    compartment_id=compartment_id,
    model_id=model_id,
)
llm_Summarizer= ChatOCIGenAI(
    service_endpoint=service_endpoint,  
    compartment_id=compartment_id,
    model_id=model_id,
)


#Define State / Schema
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage],add_messages]
    

def chat_node(state: ChatState):
    message=state["messages"]
    response=model.invoke(message).content
    return {
        "messages": [
            AIMessage(content=response)
        ]
    }

conn = sqlite3.connect(database='chatbot.db', check_same_thread=False)
# chreate Extra table to store titles
def init_threads_table(conn):
    """Initialize the chat_threads table to store thread titles."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_threads (
            thread_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
init_threads_table(conn)
checkPointer = SqliteSaver(conn=conn)



#Define Graph
graph=StateGraph(ChatState)
#add_node
graph.add_node("chat_node",chat_node)

#add_edge
graph.add_edge(START,"chat_node")
graph.add_edge("chat_node",END)

chatbot = graph.compile(checkpointer=checkPointer)

#test
# --------------------
# INVOKE (IMPORTANT)
# --------------------
# result = chatbot.invoke(
#     {
#         "messages": [HumanMessage(content="What is the capital of India?")]
#     },
#     config={"configurable": {"thread_id": "user1"}}
# )

#print("Threads:", checkPointer.list(None)) #<generator object SqliteSaver.list at 0x000002259B287680>

# for checkpoint in checkPointer.list(None):
#     print("Thread ID:", checkpoint.config)
# # repeatable threads
# # Thread ID: user1
# # Thread ID: user1
# # Thread ID: user1


def retrieve_allThreads():
    """Retrieve all unique thread IDs from the checkpoints stored in the database.
        Arguments:
        None    
        Returns:
        A list of unique thread IDs.
    """
    all_threads =set()
    for checkpoint in checkPointer.list(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)

def save_thread_title(thread_id: str, title: str):
    """Save or update the title for a given thread ID."""
    conn.execute(
        """
        INSERT INTO chat_threads (thread_id, title)
        VALUES (?, ?)
        ON CONFLICT(thread_id)
        DO UPDATE SET title = excluded.title
        """,
        (thread_id, title)
    )
    conn.commit()

def get_thread_title(thread_id: str) -> str:
    """Retrieve the title for a given thread ID. 
        Arguments:
        thread_id: The ID of the chat thread.
        Returns:
        The title of the chat thread, or "New Chat" if not found.
    """
    cursor = conn.execute(
        "SELECT title FROM chat_threads WHERE thread_id = ?",
        (thread_id,)
    )
    row = cursor.fetchone()
    return row[0] if row else "New Chat"
