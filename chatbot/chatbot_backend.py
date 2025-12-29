from langgraph.graph import StateGraph, START, END
from typing import TypedDict,Annotated
from langchain_core.messages import BaseMessage,HumanMessage,AIMessage
from langchain_oci import ChatOCIGenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
import os

load_dotenv()

service_endpoint = os.getenv("OCI_SERVICE_ENDPOINT")
compartment_id = os.getenv("OCI_COMPARTMENT_ID")
model_id = os.getenv("OCI_MODEL_ID")

model = ChatOCIGenAI(
    service_endpoint=service_endpoint,
    compartment_id=compartment_id,
    model_id=model_id,
)

# query=input("type here: ")

# while True:
#     if (query.lower()=="exit")

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

#checkPointer
checkpointer=InMemorySaver()

graph=StateGraph(ChatState)
#add_node
graph.add_node("chat_node",chat_node)

#add_edge
graph.add_edge(START,"chat_node")
graph.add_edge("chat_node",END)

chatbot = graph.compile(checkpointer=checkpointer)


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

# # --------------------
# # OUTPUT
# # --------------------
# for msg in result["messages"]:
#     print(type(msg).__name__, ":", msg.content)