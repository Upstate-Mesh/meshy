MAX_MSG_BYTES = 133
# Overhead per chunk when split: " [xx/xx]" suffix (max 9 bytes)
SPLIT_OVERHEAD = 9


def _blen(text):
    return len(text.encode("utf-8"))


def _wrap_by_bytes(text, max_bytes):
    """Word-wrap text so each chunk's UTF-8 byte length <= max_bytes."""
    words = text.split()
    chunks = []
    current = []
    current_bytes = 0
    for word in words:
        word_bytes = _blen(word)
        space = 1 if current else 0
        if current and current_bytes + space + word_bytes > max_bytes:
            chunks.append(" ".join(current))
            current = [word]
            current_bytes = word_bytes
        else:
            current.append(word)
            current_bytes += space + word_bytes
    if current:
        chunks.append(" ".join(current))
    return chunks


def split_message(text):
    if not text:
        return []
    if _blen(text) <= MAX_MSG_BYTES:
        return [text]

    chunks = _wrap_by_bytes(text, MAX_MSG_BYTES - SPLIT_OVERHEAD)
    n = len(chunks)
    return [f"{chunk} [{i + 1}/{n}]" for i, chunk in enumerate(chunks)]


def node_label(contact):
    name = contact.get("adv_name", "") if contact else "unknown"
    key = (contact.get("public_key", "") if contact else "unknown")[:12]
    return f"{name} ({key})" if name else f"({key})"
