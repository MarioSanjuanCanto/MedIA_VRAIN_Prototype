"""Keep user intent separate from frontend command instructions."""
MARKER = 'Petición del usuario:'


def user_request_text(message: str) -> str:
    return message.rsplit(MARKER, 1)[-1].strip()


def legacy_command(message: str) -> tuple[str, str]:
    """Accept old clients/retries without routing on expanded instructions."""
    if MARKER not in message:
        return message, ''
    for prefix, command in (
        ('Resume el vídeo indicado', '/resumir'),
        ('Extrae un clip con Media Core.', '/clip'),
    ):
        if message.startswith(prefix):
            return f'{command} {user_request_text(message)}'.strip(), message
    return message, ''
