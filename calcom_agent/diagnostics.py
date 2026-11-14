"""Allowlisted user-facing failures. Never include raw provider exceptions/data."""

import re
import math
from urllib.parse import urlsplit


MESSAGES = {
    "choose_series_end": ("The assistant supplied two conflicting recurrence end conditions", "The assistant should retry with only the resolved end date, omitting the week count. No booking was created."),
    "attendee_email_rejected": ("Cal.com rejected the attendee email address", "Verify the exact attendee address and that its domain exists and receives email. A valid-looking address may still fail Cal.com’s mail-domain checks. Provide a corrected address to prepare a new proposal."),
    "booking_validation_rejected": ("Cal.com rejected the booking details (400)", "The provider rejected validation, but did not identify a recognized cause. Check attendee email, template fields and availability; do not assume a booking was created."),
    "recurrence_rejected": ("Cal.com rejected the recurrence settings", "Use a bounded weekly schedule with specified weekdays, time and number of weeks, then review every occurrence before confirmation."),
    "nonexistent_local_time": ("That local time does not exist because of the daylight saving clock change", "Choose a time before or after the skipped hour."),
    "ambiguous_local_time": ("That local time occurs twice during the daylight saving clock change", "Specify the first or second occurrence, or choose another time."),
    "unsupported_custom_title": ("The selected template uses automatic attendee/host 1:1 naming", "Use the inferred 1:1 title or configure a template with the desired naming pattern. No booking was created."),
    "verification_failed": ("Cal.com returned a write result, but a fresh read did not verify the intended change", "Do not repeat the write. Inspect current bookings and the destination calendar first; the booking may already exist."),
    "title_template_not_configured": ("This template does not use the inferred meeting title", "Set the template’s custom event name to {Scheduler first name}/{Organiser first name} 1:1 in Cal.com, then retry. No booking was created."),
    "initialization_failed": ("The assistant could not initialize", "Check the Python dependencies and private configuration, then restart Streamlit from the repository root."),
    "unclassified_failure": ("A service request failed", "Check the model connection and account setup. Raw provider details are hidden; inspect Cal.com before repeating a change."),
    "model_connection_failed": ("Cannot connect to Ollama", "Check Model connection, the server address, the daemon on Ubuntu, and network permissions of the process running Streamlit."),
    "model_unavailable": ("Ollama is unavailable", "Check the Model connection panel and start Ollama on the same Ubuntu host as Streamlit."),
    "model_connection_dropped": ("Ollama disconnected during generation", "Check the Ollama process and GPU/system memory on Ubuntu before retrying."),
    "model_timeout": ("Ollama did not respond in time", "Check model loading, GPU/system memory and whether other requests are using Ollama."),
    "model_not_installed": ("The selected model is not installed", "Match OLLAMA_MODEL to an exact tag from 'ollama list' on Ubuntu, then restart Streamlit."),
    "invalid_model_response": ("Ollama returned an invalid or incomplete reply", "Check the selected model's tool support and Ollama version. Try a fresh conversation."),
    "unauthorized": ("Cal.com rejected authentication", "Check CAL_API_KEY privately and remove stale environment overrides. Never paste the key into chat."),
    "forbidden": ("Cal.com denied access", "Verify the API key belongs to the account that owns this event type or booking."),
    "account_mismatch": ("The configured Cal.com username and API key belong to different accounts",
                         "Set CAL_USERNAME in .env to the profile username of the API key owner, or use an API key from the intended account. Restart the app and reset the conversation. Public templates from another profile can be listed but cannot be used as this account’s templates."),
    "not_found": ("Cal.com could not find the requested resource", "Refresh event types/bookings and use an API-returned ID from the correct account."),
    "rate_limited": ("Cal.com is rate limiting requests", "Wait before trying again. Inspect the account before retrying any uncertain write."),
    "timeout": ("Cal.com did not respond in time", "Check connectivity. A fresh read is safe; inspect Cal.com before repeating a write."),
    "network": ("Cannot reach Cal.com", "Check Ubuntu internet access and runtime network restrictions."),
    "server_error": ("Cal.com reported a server error", "Try a read later. Inspect bookings before attempting another write."),
    "http_error": ("Cal.com rejected the request", "Check the HTTP status below, account permissions and event configuration. Refresh availability before preparing another change."),
    "invalid_response": ("Cal.com returned an unexpected response", "Check API version compatibility. Do not assume a write failed; inspect Cal.com first."),
    "write_outcome_unknown": ("The write outcome is unknown", "Do not repeat the write. Inspect Cal.com to determine whether the booking or template was created/changed, then refresh this conversation."),
    "slot_unavailable": ("The selected slot is no longer available", "Request fresh availability and select another slot."),
    "stale_confirmation": ("The proposal changed before confirmation", "Refresh the booking and prepare a new proposal for review."),
    "confirmation_expired": ("The confirmation expired", "Prepare a fresh proposal and confirm its current details."),
    "invalid_confirmation": ("There is no current matching confirmation", "Prepare a new proposal. Repeated confirmations do not repeat writes."),
    "unsupported_event_type": ("This event type is outside the supported scope", "Use a free standard personal event type without seats, recurrence or email verification."),
    "discover_event_type_first": ("The meeting template ID has not been verified", "The assistant should refresh event types and use an ID returned by Cal.com."),
    "reuse_existing_event_type": ("A matching meeting template already exists", "Refresh event types and use the existing template for availability and booking."),
    "event_type_slug_exists": ("That meeting template link already exists", "Reuse the existing template or choose a different link slug."),
    "missing_information": ("Scheduling details are missing", "Supply the attendee or booking details requested in chat, then prepare the proposal again."),
    "unsupported_duration": ("This template does not support the requested duration", "Choose a supported duration or confirm creation of a new matching template."),
    "ambiguous_booking": ("More than one booking matches", "List bookings and select one numbered result before preparing a change."),
    "booking_not_found": ("The booking reference does not match saved candidates", "Refresh the booking list and select a current numbered result."),
    "invalid_tool_arguments": ("The model supplied invalid tool arguments", "The assistant can retry with corrected structured arguments or ask for clarification."),
    "unknown_tool": ("The model requested an unsupported tool", "No unsupported tool was executed. Narrow the request or try a fresh conversation."),
    "booking_date_weekday_mismatch": ("The date does not match the requested weekday", "Correct the date using the saved weekday before preparing the booking."),
    "booking_draft_time_mismatch": ("The proposed time differs from the saved request", "Reuse the saved date and time, or update the draft when the user changes them."),
    "invalid_allow_conflicts": ("Invalid overlap option", "Use a true or false overlap option and prepare a new proposal."),
    "relative_date_mismatch": ("The model resolved a relative date incorrectly", "Python rejected that date. Review the corrected absolute date before confirming."),
    "tool_limit": ("The assistant reached its tool limit", "Narrow the request or reset the conversation. No new action was executed."),
    "unexpected_error": ("An unexpected application error interrupted the request", "Conversation context was reset. Inspect Cal.com before retrying a change; the request was not replayed."),
    "request_interrupted": ("The request was interrupted", "Context was reset and the request was not replayed. Inspect Cal.com before repeating a change."),
}


def diagnostic(stage: str, code: str, *, status=None, fatal=True, details=None) -> dict:
    """Return only fixed text, known codes, numeric statuses and a fixed stage."""
    match = re.fullmatch(r"model_http_error_(\d{3})", code)
    if match:
        status = int(match[1])
        summary = "Ollama rejected the model request"
        hint = {400: "Check model tool/thinking support and Ollama version.",
                404: "Verify the selected installed model and server URL using Model connection.",
                500: "Check the Ollama process and available GPU/system memory on Ubuntu."}.get(
                    status, "Check Ollama availability and the HTTP status. Raw response details are intentionally hidden.")
    else:
        if code not in MESSAGES:
            code = "tool_validation_error" if not fatal else "unclassified_failure"
        summary, hint = MESSAGES.get(code, ("A tool request needs correction", "The assistant can correct its arguments or ask for clarification. No invalid action was executed."))
    context = {}
    details = details if isinstance(details, dict) else {}
    for key in ("elapsed_seconds", "read_timeout_seconds", "connect_timeout_seconds", "write_timeout_seconds", "pool_timeout_seconds", "message_count", "tool_count", "context_tokens",
                "max_output_tokens", "automatic_retries", "model_size_gib", "vram_gib"):
        value = details.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e9:
            context[key] = value
    for key in ("streaming", "thinking", "model_loaded"):
        if type(details.get(key)) is bool:
            context[key] = details[key]
    choices = {"timeout_phase": {"connect", "read", "write", "pool", "unknown"},
               "method": {"POST"}, "path": {"/api/chat"},
               "placement": {"cpu_only", "gpu_or_mixed"}, "runtime_check": {"available", "unavailable"}}
    for key, allowed in choices.items():
        if isinstance(details.get(key), str) and details[key] in allowed:
            context[key] = details[key]
    model = details.get("model")
    if isinstance(model, str) and re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,128}", model):
        context["model"] = model
    server = details.get("server")
    if isinstance(server, str):
        try:
            url = urlsplit(server)
            if url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password and not url.query and not url.fragment:
                context["server"] = server if url.path in {"", "/"} else "origin_hidden"
        except ValueError:
            pass
    categories = details.get("server_error_categories")
    if isinstance(categories, list):
        context["server_error_categories"] = [c for c in categories if isinstance(c, str) and c in
                                              {"memory_allocation", "gpu_or_device", "runner_process", "context_limit"}]
    if code == "model_timeout" and "elapsed_seconds" in context:
        summary = f"Ollama {context.get('timeout_phase', 'request')} timeout after {context['elapsed_seconds']} seconds"
        hint = ("The client stopped waiting; no generation retry was sent. Inspect 'ollama ps' and 'nvidia-smi' on Ubuntu. "
                "For slow CPU inference, OLLAMA_TIMEOUT_SECONDS is configurable (1–600); restart Streamlit after changing it. "
                "The timeout does not cancel or prove completion of server-side generation.")
    if context.get("placement") == "cpu_only":
        hint += " Runtime evidence: the selected loaded model has zero VRAM allocation and is running on CPU."
    if "gpu_or_device" in context.get("server_error_categories", []):
        hint += " Ollama's error matched a GPU/device failure category; check NVIDIA driver health."
    return {"stage": stage if stage in {"model", "calendar", "execution", "tool", "application"} else "application",
            "code": code, "summary": summary, "next_step": hint,
            "http_status": status if type(status) is int and 100 <= status <= 599 else None,
            "fatal": fatal is True, "context": context}
