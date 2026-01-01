from langgraph.graph import StateGraph, START, END
from typing import TypedDict,Annotated
from langchain_core.messages import BaseMessage,HumanMessage,AIMessage,SystemMessage
from langchain_oci import ChatOCIGenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
from langchain_community.tools import DuckDuckGoSearchRun
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.tools import tool
import os,sqlite3,requests

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
SYSTEM_PROMPT = SystemMessage(
    content="""
You are an assistant with access to tools.

Rules:
- Use DuckDuckGoSearchRun for current or factual queries.
- Use calculator for math operations.
- Use get_stock_price for stock prices.
- If a tool is needed, ALWAYS call it.
"""
)

#----------Tools Definition ---------#
search_tool = DuckDuckGoSearchRun()

@tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """
    Perform a basic arithmetic operation on two numbers.
    Supported operations: add, sub, mul, div
    """
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return {"error": "Division by zero is not allowed"}
            result = first_num / second_num
        else:
            return {"error": f"Unsupported operation '{operation}'"}
        
        return {"first_num": first_num, "second_num": second_num, "operation": operation, "result": result}
    except Exception as e:
        return {"error": str(e)}

stock_api_key=os.getenv("STOCK_API_KEY")

@tool
def get_stock_price(symbol: str) -> dict:
    """
    Fetch the current stock price for a given symbol .
    The symbol must be a valid stock ticker like AAPL, TSLA, MSFT.
    """
    url=f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={stock_api_key}"
    response=requests.get(url)
    return response.json()

tools=[search_tool, calculator, get_stock_price]
model_with_tools=model.bind_tools(tools)


#Define State / Schema
class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage],add_messages]
    

def chat_node(state: ChatState):
    """LLM node that may answer or request a tool call."""
    message=[SYSTEM_PROMPT] + state["messages"]
    response=model_with_tools.invoke(message)
    return {"messages": [response]}

tool_node = ToolNode(tools)  #ToolNode executes the tool calls produced by the LLM and returns the results back into the graph state.
 

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
graph.add_node("tools", tool_node) 

#add_edge
graph.add_edge(START,"chat_node")
graph.add_conditional_edges("chat_node",tools_condition)
graph.add_edge("tools","chat_node")

# Flow:
# START -> chat_node (LLM reasoning)
#
# tools_condition inspects the last AI message:
# - If the LLM requested a tool (tool_calls present),
#   route execution to the "tools" node.
# - If no tool was requested, tools_condition returns END,
#   which terminates the graph.
#
# Hence, we do not explicitly connect chat_node to END.
# The END transition is implicitly handled by tools_condition.

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
