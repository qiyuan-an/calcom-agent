"""Run from the repository root: streamlit run app.py."""

import streamlit as st

from calcom_agent.config import ConfigError
from calcom_agent.ui_session import create_session
from calcom_agent.ollama import OllamaProvider
from calcom_agent.diagnostics import diagnostic


def show_diagnostic(detail):
    st.text(detail["summary"])
    st.text("Next step: " + detail["next_step"])
    st.code("\n".join([f"Stage: {detail['stage']}", f"Code: {detail['code']}"] +
                      ([f"HTTP status: {detail['http_status']}"] if detail["http_status"] else [])), language=None)
    if detail.get("context"):
        st.json(detail["context"])


st.set_page_config(page_title="Scheduling assistant", page_icon="📅")
st.title("Scheduling assistant")
st.caption("Manage your Cal.com bookings through conversation.")
st.info("Review each proposed change, then reply ‘confirm’ to apply it. Cal.com may send calendar invitations and updates.")

if "chat_session" not in st.session_state:
    try:
        st.session_state.chat_session = create_session()
    except ConfigError as error:
        st.error(f"Configuration needed: {error}. Update your private .env and reload.")
        st.stop()
    except Exception:
        st.error("Unable to initialize the assistant. Check your local configuration and reload.")
        with st.expander("Troubleshooting details"):
            show_diagnostic(diagnostic("application", "initialization_failed"))
        st.stop()

session = st.session_state.chat_session
st.caption(f"Timezone: {session.conversation.time_zone} · Cal.com bookings only")

if isinstance(session.conversation.model, OllamaProvider):
    with st.expander("Model connection"):
        settings = session.conversation.model.connection_settings()
        st.text(f"Model: {settings['model']}\nOllama server: {settings['server']}\nRead timeout: {settings['read_timeout_seconds']} seconds")
        if st.button("Inspect loaded model", disabled=session.busy):
            st.json(session.conversation.model.runtime_snapshot())
        if st.button("Check model connection", disabled=session.busy):
            status = session.conversation.model.check_connection()["status"]
            if status == "ready":
                st.success("Ollama is reachable and the selected model is installed. This checks metadata, not generation.")
            else:
                detail = diagnostic("model", status)
                st.error(detail["summary"])
                show_diagnostic(detail)

if st.button("Reset conversation", disabled=session.busy):
    session.reset()

if not session.messages:
    st.markdown("Try **‘What’s on my calendar tomorrow?’** or **‘Book a 30-minute intro Thursday afternoon.’**")

for message in session.messages:
    with st.chat_message(message["role"]):
        # Keep arbitrary user/model text out of HTML and unsafe markup rendering.
        st.text(message["content"])
        outputs = message.get("model_outputs", [])
        if outputs:
            with st.expander("Model output", expanded=False):
                for output in outputs:
                    st.text(output)
        details = message.get("diagnostics", [])
        if details:
            for detail in details:
                if detail["fatal"]:
                    st.error(detail["summary"])
            with st.expander("Troubleshooting details"):
                for detail in details:
                    show_diagnostic(detail)


def submit():
    session.enqueue(st.session_state.get("chat_prompt", ""))


st.chat_input("Ask about your bookings…", key="chat_prompt", max_chars=12000,
              disabled=session.busy, on_submit=submit)

if session.has_queued:
    with st.spinner("Working on your request…"):
        session.process()
    st.rerun()
elif session.busy:
    st.caption("A request is already in progress. Please wait for it to finish.")
