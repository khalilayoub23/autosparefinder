# ==============================================================================
# ABANDONED — DO NOT USE
# ------------------------------------------------------------------------------
# Minimal Streamlit prototype for the BERT-based SalesAgent ("John").
# Not part of the production frontend stack.
#
# The production frontend is a React/Vite SPA in:
#   frontend/src/   (built via npm run build, served by nginx)
#
# Kept for git history only. Do not run, import, or extend this file.
# ==============================================================================

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
