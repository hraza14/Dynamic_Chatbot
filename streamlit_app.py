import os
import tempfile
import urllib.request
from pathlib import Path

import streamlit as st

import agent
import db
import import_data
import llm

# Session configuration for Streamlit
st.set_page_config(page_title="Cricket Data Chatbot", page_icon="🏏", layout="wide")

if "db_path" not in st.session_state:
    # Create a unique temporary database file for this session
    # This completely avoids permanent disk usage on Render!
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st.session_state.db_path = Path(path)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# --- Sidebar: Settings & Data Upload ---
with st.sidebar:
    st.title("⚙️ Settings")
    
    provider = st.selectbox("Provider", ["OpenRouter / OpenAI Compatible", "Anthropic", "Ollama"])
    if provider == "OpenRouter / OpenAI Compatible":
        endpoint = st.text_input("Endpoint (Base URL)", value="https://openrouter.ai/api/v1")
        model = st.text_input("Model", value="deepseek/deepseek-chat:free")
    elif provider == "Anthropic":
        endpoint = st.text_input("Endpoint (Base URL)", value="https://api.anthropic.com")
        model = st.text_input("Model", value="claude-3-haiku-20240307")
    else:
        endpoint = st.text_input("Endpoint (Base URL)", value="http://localhost:11434")
        model = st.text_input("Model", value="llama3.2:3b")

    api_key = st.text_input("API Key", type="password")
    
    st.session_state.llm_settings = {
        "provider": "openai" if provider == "OpenRouter / OpenAI Compatible" else ("anthropic" if provider == "Anthropic" else "ollama"),
        "endpoint": endpoint.strip(),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "num_ctx": 8192
    }
    
    if st.button("Test Connection"):
        try:
            res = llm.test_connection(st.session_state.llm_settings)
            st.success(f"Success! Model reply: {res['reply']}")
        except Exception as e:
            st.error(f"Connection failed: {e}")

    st.markdown("---")
    st.title("📁 Data Source")
    st.markdown("Upload a file or provide a URL to load into the session memory.")
    
    uploaded_file = st.file_uploader("Upload CSV/XLSX", type=["csv", "xlsx", "xlsm"])
    dataset_url = st.text_input("Or fetch from Web URL (CSV/XLSX)")

    if st.button("Load Data"):
        if uploaded_file is not None:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir) / uploaded_file.name
                with open(tmp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                try:
                    summary = import_data.import_file(tmp_path, st.session_state.db_path, fresh=True, table_hint=Path(uploaded_file.name).stem)
                    st.success(f"Successfully loaded! Tables: {list(summary.keys())}")
                except Exception as e:
                    st.error(f"Import failed: {e}")
        elif dataset_url.strip():
            try:
                st.info("Downloading from URL...")
                with tempfile.TemporaryDirectory() as tmpdir:
                    fname = dataset_url.split("/")[-1]
                    if not fname.lower().endswith((".csv", ".xlsx", ".xlsm")):
                        fname = "downloaded.csv"  # fallback
                    tmp_path = Path(tmpdir) / fname
                    urllib.request.urlretrieve(dataset_url.strip(), tmp_path)
                    summary = import_data.import_file(tmp_path, st.session_state.db_path, fresh=True, table_hint=Path(fname).stem)
                    st.success(f"Successfully fetched and loaded! Tables: {list(summary.keys())}")
            except Exception as e:
                st.error(f"Failed to fetch or import URL: {e}")
        else:
            st.warning("Please upload a file or provide a URL.")


# --- Main Chat Interface ---
st.title("🏏 Cricket Data Chatbot")

# Display chat messages from history
for message in st.session_state.chat_history:
    if message.get("question"):
        with st.chat_message("user"):
            st.markdown(message["question"])
    if message.get("answer"):
        with st.chat_message("assistant"):
            st.markdown(message["answer"])
            with st.expander("Show SQL"):
                st.code(message.get("sql", ""), language="sql")

# Chat input
if prompt := st.chat_input("Ask a question about the data..."):
    # Check if DB has data
    if not db.exists(st.session_state.db_path):
        st.error("⚠️ No data loaded yet. Please use the sidebar to upload a dataset or fetch from a URL.")
        st.stop()
        
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing..."):
            try:
                result = agent.ask(
                    prompt, 
                    st.session_state.chat_history, 
                    st.session_state.llm_settings, 
                    db_path=st.session_state.db_path, 
                    summarize=True
                )
                
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.markdown(result["answer"])
                    with st.expander("Show SQL"):
                        st.code(result.get("sql", ""), language="sql")
                    
                    # Update history
                    st.session_state.chat_history.append({
                        "question": prompt,
                        "sql": result.get("sql"),
                        "answer": result.get("answer")
                    })
            except Exception as e:
                st.error(f"Unexpected error: {e}")

