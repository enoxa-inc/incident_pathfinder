"""Session state: DynamoDB in AWS, in-memory locally (STORE_BACKEND=memory)."""
import json
import os
import time

TABLE_NAME = os.environ.get("TABLE_NAME", "incident-pathfinder-sessions")
BACKEND = os.environ.get("STORE_BACKEND", "dynamodb")
TTL_SECONDS = 24 * 3600

_memory = {}
_table = None


def _ddb():
    global _table
    if _table is None:
        import boto3
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def load(session_id):
    if BACKEND == "memory":
        return _memory.get(session_id, {})
    item = _ddb().get_item(Key={"session_id": session_id}).get("Item")
    return json.loads(item["state"]) if item else {}


def save(session_id, state):
    if BACKEND == "memory":
        _memory[session_id] = state
        return
    _ddb().put_item(Item={"session_id": session_id, "state": json.dumps(state, default=str),
                          "updated_at": int(time.time()), "expires_at": int(time.time()) + TTL_SECONDS})
