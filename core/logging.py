import logging
import re

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token|authorization)\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = tuple(
                redact_text(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
    redact_filter = SecretRedactionFilter()
    for handler in root.handlers:
        if not any(isinstance(existing, SecretRedactionFilter) for existing in handler.filters):
            handler.addFilter(redact_filter)
