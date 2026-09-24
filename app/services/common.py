import uuid

from app.domain.exceptions import ValidationError


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)
