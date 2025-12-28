import streamlit as st 
from chatbot_backend import chatbot
from langchain_core.messages import HumanMessage

config={"configurable":{"thread_id":"user-1"}}

if 'messageHistory' not in st.session_state:
    st.session_state['messageHistory']=[]

#loading the conservation history
for message in st.session_state['messageHistory']:
    with st.chat_message(message['role']):
        st.text(message['content'])

#{'role': 'user', 'content': 'Hi'}
#{'role': 'assistant', 'content': 'Hello'}

user_input=st.chat_input('Type here')

if user_input:
    
    st.session_state['messageHistory'].append({'role':'user','content':user_input})
    with st.chat_message('user'):
        st.text(user_input)
    
    response=chatbot.invoke({'messages': [HumanMessage(content=user_input)]}, config=config)

    with st.chat_message('assistant'):
        ai_message=st.write_stream(
            message_chunk.content for message_chunk,metadata in chatbot.stream(
                {'messages':[HumanMessage(content=user_input)]},
                config=config,
                stream_mode='messages'
            )
        )
    st.session_state['messageHistory'].append({'role':'assistant','content':ai_message})