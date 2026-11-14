from pathlib import Path
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from unittest.mock import Mock, patch

import pytest
from streamlit.testing.v1 import AppTest

from calcom_agent.client import Booking, EventType
from calcom_agent.config import ConfigError
from calcom_agent.conversation import Conversation
from calcom_agent.model import ModelError, ModelReply, ToolCall
from calcom_agent.ui_session import ChatSession


def synthetic_session(*replies):
    model = Mock()
    model.chat.side_effect = list(replies)
    client = Mock()
    now = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
    start = now + timedelta(days=1)
    client.list_bookings.return_value = [Booking("demo-uid", "Demo booking", "accepted", start,
                                                 start + timedelta(minutes=30), 1)]
    client.get_event_type.return_value = EventType(1, "Demo intro", 30, [], [])
    executor = Mock()
    executor.execute.return_value = {"status": "cancelled"}
    session = ChatSession(Conversation(model, client, executor=executor, clock=lambda: now))
    return session, model, executor


def call(name, **args):
    return ModelReply(calls=(ToolCall(name, args),))


def test_streamlit_chat_rerun_reset_and_two_independent_sessions():
    one, model_one, _ = synthetic_session(ModelReply("First session answer"))
    two, model_two, _ = synthetic_session(ModelReply("Second session answer"))
    with patch("calcom_agent.ui_session.create_session", side_effect=[one, two]) as factory:
        first = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        second = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        assert not first.exception and not second.exception
        first.chat_input[0].set_value("First session question").run()
        assert [m.value for m in first.text] == ["First session question", "First session answer"]
        first.run()  # Ordinary rerun cannot repeat the model request.
        assert model_one.chat.call_count == 1
        assert not second.chat_message and not two.messages
        second.chat_input[0].set_value("Second session question").run()
        assert model_two.chat.call_count == 1
        first.button[0].click().run()
        assert not first.chat_message and not one.conversation.history
        assert two.messages[-1]["content"] == "Second session answer"
        assert factory.call_count == 2


def test_streamlit_confirmation_does_not_repeat_on_rerun_or_duplicate_yes():
    session, model, executor = synthetic_session(call("list_bookings"), call("prepare_cancel", number=1),
                                                 ModelReply("There is no pending action."))
    with patch("calcom_agent.ui_session.create_session", return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        app.chat_input[0].set_value("Cancel the first booking").run()
        assert session.conversation.pending
        executor.execute.assert_not_called()
        app.chat_input[0].set_value("confirm").run()
        assert executor.execute.call_count == 1
        app.run()
        app.chat_input[0].set_value("confirm").run()
        assert executor.execute.call_count == 1
        assert not app.exception


def test_outage_and_configuration_errors_are_readable_and_sanitized():
    session, _, _ = synthetic_session(ModelError("model_unavailable"))
    with patch("calcom_agent.ui_session.create_session", return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        app.chat_input[0].set_value("List my bookings").run()
        assert any("model_unavailable" in text.value for text in app.text)
        assert not app.exception
    with patch("calcom_agent.ui_session.create_session", side_effect=ConfigError("Missing or invalid CAL_API_KEY")):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        assert "CAL_API_KEY" in app.error[0].value
        assert not app.chat_input and not app.exception
    with patch("calcom_agent.ui_session.create_session", side_effect=RuntimeError("synthetic-secret")):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        assert "synthetic-secret" not in app.error[0].value
        assert not app.exception


def test_slow_request_rejects_concurrent_submission_reset_and_replay():
    session, model, _ = synthetic_session()
    entered, release = Event(), Event()
    def slow(*args):
        entered.set()
        assert release.wait(5)
        return ModelReply("Completed")
    model.chat.side_effect = slow
    assert session.enqueue("Request", request_id="event-1")
    worker = Thread(target=session.process)
    worker.start()
    try:
        assert entered.wait(5)
        assert session.busy
        assert not session.enqueue("Duplicate", request_id="event-2")
        assert not session.reset()
        assert not session.process()
        # Disabled controls are also verified in the rendered app during processing.
        with patch("calcom_agent.ui_session.create_session", return_value=session):
            app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
            assert app.chat_input[0].disabled and app.button[0].disabled
            assert not app.exception
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not session.busy
    assert not session.enqueue("Replay", request_id="event-1")
    assert not session.process()
    assert model.chat.call_count == 1
    assert session.messages[-1]["content"] == "Completed"


def test_unexpected_failure_consumes_request_and_hides_exception():
    session, model, _ = synthetic_session(RuntimeError("synthetic-secret"))
    assert session.enqueue("Request", request_id="event-1")
    assert session.process()
    assert "synthetic-secret" not in session.messages[-1]["content"]
    assert "context was reset" in session.messages[-1]["content"]
    assert not session.conversation.history
    assert not session.enqueue("Replay", request_id="event-1")
    assert not session.process()
    model.chat.side_effect = KeyboardInterrupt()
    assert session.enqueue("Interrupted request", request_id="event-2")
    with pytest.raises(KeyboardInterrupt):
        session.process()
    assert not session.busy and not session.conversation.history
    assert "interrupted" in session.messages[-1]["content"]
    assert not session.enqueue("Replay", request_id="event-2")


def test_session_factory_passes_only_model_settings_to_provider():
    from calcom_agent.config import Settings
    from calcom_agent.ui_session import create_session
    settings = Settings("synthetic-secret", "demo", ollama_model="configured-model",
                        ollama_base_url="http://localhost:11434", default_timezone="UTC")
    with patch("calcom_agent.ui_session.load_settings", return_value=settings), \
         patch("calcom_agent.ui_session.CalcomClient") as client, \
         patch("calcom_agent.ui_session.OllamaProvider") as provider:
        session = create_session()
        provider.assert_called_once_with("configured-model", "http://localhost:11434", timeout_seconds=120)
        assert session.conversation.time_zone == "UTC"
        assert session.conversation.executor is client.return_value


def test_model_connection_panel_checks_actual_provider_without_prompt_or_credentials():
    import httpx
    from calcom_agent.ollama import OllamaProvider
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'models': [{'name': 'demo-model'}]})
    provider = OllamaProvider('demo-model', transport=httpx.MockTransport(handler))
    session = ChatSession(Conversation(provider, Mock()))
    try:
        with patch('calcom_agent.ui_session.create_session', return_value=session):
            app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
            assert 'demo-model' in app.text[0].value
            check = next(button for button in app.button if button.label == 'Check model connection')
            check.click().run()
            assert 'reachable' in app.success[0].value
            assert len(requests) == 1 and requests[0].method == 'GET'
            assert not session.messages and not app.exception
    finally:
        provider.close()


def test_structured_model_failure_shows_status_and_survives_rerun():
    session, _, _ = synthetic_session(ModelError('model_http_error_404'), ModelReply('Recovered'))
    with patch('calcom_agent.ui_session.create_session', return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
        app.chat_input[0].set_value('Request').run()
        assert app.error[0].value == 'Ollama rejected the model request'
        assert any('HTTP status: 404' in code.value and 'Stage: model' in code.value for code in app.code)
        assert any(expander.label == 'Troubleshooting details' for expander in app.expander)
        app.run()
        assert any('HTTP status: 404' in code.value for code in app.code)
        app.chat_input[0].set_value('Try again').run()
        assert session.messages[-1]['diagnostics'] == []
        assert session.messages[-2]['role'] == 'user'
        assert not app.exception


def test_calendar_tool_failure_is_visible_even_after_model_reply():
    from calcom_agent.client import CalcomError
    session, _, _ = synthetic_session(call('list_bookings'), ModelReply('Cannot fetch bookings.'))
    session.conversation.client.list_bookings.side_effect = CalcomError('forbidden', 403)
    with patch('calcom_agent.ui_session.create_session', return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
        app.chat_input[0].set_value('List bookings').run()
        assert any('Stage: calendar' in code.value and 'HTTP status: 403' in code.value for code in app.code)
        assert any('owns this event type' in text.value for text in app.text)
        assert not app.error  # Handled tool issue, not a claimed terminal failure.
        assert not app.exception


def test_unknown_model_error_never_renders_private_provider_text():
    session, _, _ = synthetic_session(ModelError('synthetic-secret private calendar response'))
    with patch('calcom_agent.ui_session.create_session', return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
        app.chat_input[0].set_value('Request').run()
        rendered = ' '.join(element.value for elements in (app.text, app.code, app.error) for element in elements)
        assert 'synthetic-secret' not in rendered and 'private calendar response' not in rendered
        assert 'unclassified_failure' in rendered
        assert not app.exception


def test_timeout_panel_renders_measured_context_and_sanitizes_extra_fields():
    session, _, _ = synthetic_session(ModelError('model_timeout', details={
        'elapsed_seconds': 60.2, 'read_timeout_seconds': 60, 'timeout_phase': 'read',
        'model': 'demo-model', 'server': 'http://127.0.0.1:11434', 'message_count': 4,
        'tool_count': 7, 'placement': 'cpu_only', 'model_loaded': True,
        'raw_response': 'synthetic-secret', 'prompt': 'private appointment'}))
    with patch('calcom_agent.ui_session.create_session', return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run()
        app.chat_input[0].set_value('Request').run()
        assert '60.2' in app.error[0].value
        rendered = ' '.join(element.value for elements in (app.text, app.code, app.error) for element in elements)
        assert 'CPU' in rendered
        context = session.messages[-1]['diagnostics'][0]['context']
        assert context['timeout_phase'] == 'read' and context['read_timeout_seconds'] == 60
        assert 'raw_response' not in context and 'prompt' not in context
        assert len(app.json) == 1 and not app.exception


def test_unfinished_model_output_is_collapsed_and_not_a_chat_message():
    draft = "Okay, let's see. I need to check the slots."
    session, _, _ = synthetic_session(ModelReply(draft, incomplete=True), ModelReply('Which time would you prefer?'))
    with patch("calcom_agent.ui_session.create_session", return_value=session):
        app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
        app.chat_input[0].set_value('Move it to afternoon').run()
        assert not app.exception
        assert len(session.messages) == 2
        assert session.messages[-1]['content'] == 'Which time would you prefer?'
        assert session.messages[-1]['model_outputs'] == [draft]
        output = next(e for e in app.expander if e.label == 'Model output')
        assert output.proto.expanded is False
        app.run()
        assert len(session.messages) == 2
