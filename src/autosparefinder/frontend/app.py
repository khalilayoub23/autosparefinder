import streamlit as st
from agents.sales_agent import SalesAgent
import torch

def create_john_interface():
    st.title("🤖 John - Smart Sales Agent")
    
    # Initialize John
    john = SalesAgent()
    
    # Chat Interface
    st.header("💬 Chat with John")
    user_input = st.text_input("Type your message here:")
    if st.button("Send"):
        with st.spinner("John is thinking..."):
            response = john.process_with_bert(user_input)
            st.write(f"John: {response}")
    
    # File Upload Section
    st.header("📁 Upload Files")
    col1, col2 = st.columns(2)
    
    with col1:
        image_file = st.file_uploader("Upload Part Image", type=['png', 'jpg'])
        if image_file:
            st.image(image_file)
            if st.button("Analyze Image"):
                result = john.process_part_image(image_file)
                st.write("Analysis Result:", result)
    
    with col2:
        audio_file = st.file_uploader("Upload Voice Message", type=['wav'])
        if audio_file and st.button("Process Voice"):
            text = john.process_voice_input(audio_file)
            st.write("Transcription:", text)

if __name__ == "__main__":
    create_john_interface()
