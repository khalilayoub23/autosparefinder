import streamlit as st
from agents.sales_agent import SalesAgent


def main():
    st.title("Sales Assistant")

    # Initialize John
    john = SalesAgent("John")

    # Create the chat interface
    user_input = st.text_input("You:", "")

    if user_input:
        response = john.respond(user_input)
        st.text(f"John: {response}")


if __name__ == "__main__":
    main()
