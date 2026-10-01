"""Structured JSON logs (one line per event) -> CloudWatch Logs via Lambda stdout."""
import json
import time


def log(event, level="INFO", **fields):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": level,
                      "event": event, **fields}, default=str))
