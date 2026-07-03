"""
Zyrcon block message schema v1.
Wraps CHIEF's structured chat replies into typed, versioned message
blocks for both the HTTP chat response and the /ws/prism push.
"""

SCHEMA_VERSION = 1

def format_chief_reply_as_blocks(chief_response: dict) -> dict:
    """
    chief_response: the dict CHIEF's /message endpoint already returns
      {run_id, pending_approval_id, assistant_message, state, step}
    Returns: a chat_message envelope per the block schema.
    """
    blocks = []

    if chief_response.get("assistant_message"):
        blocks.append({
            "block_type": "text",
            "text": chief_response["assistant_message"],
        })

    approval_id = chief_response.get("pending_approval_id")
    # Client schema (Swift Codable) types approval_id / pending_approval_id as
    # String; emit strings so an Int id does not break envelope decoding.
    approval_id_str = str(approval_id) if approval_id is not None else None
    if approval_id:
        blocks.append({
            "block_type": "approval_card",
            "approval_id": approval_id_str,
            "title": "Approval needed",
            "fields": [],
        })
        blocks.append({
            "block_type": "button_row",
            "buttons": [
                {"label": "Approve", "action_token": f"approve:{approval_id_str}", "style": "primary"},
                {"label": "Reject", "action_token": f"reject:{approval_id_str}", "style": "destructive"},
            ],
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "type": "chat_message",
        "role": "assistant",
        "blocks": blocks,
        "meta": {
            "run_id": chief_response.get("run_id"),
            "pending_approval_id": approval_id_str,
            "state": chief_response.get("state"),
            "step": chief_response.get("step"),
        },
    }
