MAX_MSG_BYTES = 133
# Overhead per chunk when split: " [xx/xx]" suffix (max 9 bytes)
SPLIT_OVERHEAD = 9


def _wrap_by_bytes(text, max_bytes):
    chunks, current = [], []
    for word in text.split():
        candidate = " ".join(current + [word])
        if current and len(candidate.encode("utf-8")) > max_bytes:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def split_message(text):
    if not text:
        return []
    if len(text.encode("utf-8")) <= MAX_MSG_BYTES:
        return [text]

    chunks = _wrap_by_bytes(text, MAX_MSG_BYTES - SPLIT_OVERHEAD)
    n = len(chunks)
    return [f"{chunk} [{i + 1}/{n}]" for i, chunk in enumerate(chunks)]


def node_label(contact):
    name = contact.get("adv_name", "") if contact else "unknown"
    key = (contact.get("public_key", "") if contact else "unknown")[:12]
    return f"{name} ({key})" if name else f"({key})"
