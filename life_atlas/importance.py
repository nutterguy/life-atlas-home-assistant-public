import re
from collections.abc import Mapping


VALID_IMPORTANCE = {"major", "medium", "minor"}
EXERCISE_CATEGORIES = {"exercise", "running"}
RACE_TITLE = re.compile(r"\b(?:race|parkrun|marathon)\b", re.IGNORECASE)
RACE_TYPE_VALUES = {"race", "racing", "competition", "competitive"}


def _metadata_records(event):
    yield event
    for key in ("metadata", "source_metadata", "activity"):
        value = event.get(key)
        if isinstance(value, Mapping):
            yield value


def is_race_event(event, *, allow_title=True):
    """Return True only for explicit race metadata or a conservative title match."""
    category = str(event.get("category", "")).strip().lower()
    if category not in EXERCISE_CATEGORIES:
        return False

    for record in _metadata_records(event):
        if record.get("is_race") is True:
            return True
        for key in ("activity_type", "workout_type", "event_type", "competition_type"):
            value = record.get(key)
            if isinstance(value, str) and value.strip().lower() in RACE_TYPE_VALUES:
                return True

    return bool(allow_title and RACE_TITLE.search(str(event.get("title", ""))))


def infer_importance(event):
    """Preserve explicit importance; otherwise classify imported exercise events."""
    explicit = event.get("importance")
    if explicit is not None:
        if explicit not in VALID_IMPORTANCE:
            raise ValueError(f"Invalid importance for {event.get('title')}")
        return explicit

    category = str(event.get("category", "")).strip().lower()
    if category in EXERCISE_CATEGORIES:
        return "medium" if is_race_event(event) else "minor"
    return "medium"
