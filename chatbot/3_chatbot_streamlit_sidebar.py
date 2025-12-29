import streamlit as st 
from chatbot_backend import chatbot
from langchain_core.messages import HumanMessage
import uuid

#------------utilities fn ---------------------#
def generate_thread_id():
    return uuid.uuid4()

def reset():
    thread_id=str(generate_thread_id())
    st.session_state['chatThreads'].append(thread_id)
    st.session_state['threadId']=thread_id
    st.session_state['messageHistory']=[]
    
def get_config(threadId: str) -> dict :
    config={
        "configurable":
            {
                "thread_id":threadId
            }
    }
    return config

def load_conservation():
    state=chatbot.get_state(config=get_config(st.session_state['threadId']))
    # Check if messages key exists in state values, return empty list if not
    return state.values.get('messages', [])


if 'messageHistory' not in st.session_state:
    st.session_state['messageHistory']=[]


#[{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hello'}]

# ----------------- Side-bar -------------------------------- #
st.sidebar.title("Q&A Chatbot")
if st.sidebar.button("New Chat"):
    reset()
    

# during first chat there will be no threadId
if 'chatThreads' not in st.session_state:
    threadId=str(generate_thread_id())
    st.session_state['chatThreads']=[]
    st.session_state['chatThreads'].append(threadId)
    #for current threadId
    st.session_state['threadId']=threadId

#st.sidebar.button(thread_id) for thread_id in st.session_state['threadId']
for thread_id in st.session_state["chatThreads"][::-1]:
    if st.sidebar.button(thread_id):
        st.session_state['threadId'] = thread_id
        st.session_state['messageHistory']=[]
        messages=load_conservation()
        temp_msg=[]
        for msg in messages:
            if isinstance(msg,HumanMessage):
                role="user"
            else:
                role="assistant"
            temp_msg.append({'role':role,'content':msg.content})
        st.session_state['messageHistory']=temp_msg
        
#Writing the conservation history
for message in st.session_state['messageHistory']:
    with st.chat_message(message['role']):
        st.text(message['content'])


# ------------------------ Main-UI --------------------------- #

user_input=st.chat_input('Type here')

if user_input:
    st.session_state['messageHistory'].append({'role':'user','content':user_input})
    with st.chat_message('user'):
        st.text(user_input)
    
    #response=chatbot.invoke({'messages': [HumanMessage(content=user_input)]}, config=get_config(st.session_state['threadId']))

    with st.chat_message('assistant'):
        ai_message=st.write_stream(
            message_chunk.content for message_chunk,metadata in chatbot.stream(
                {'messages':[HumanMessage(content=user_input)]},
                config=get_config(st.session_state['threadId']),
                stream_mode='messages'
            )
        )
    st.session_state['messageHistory'].append({'role':'assistant','content':ai_message})