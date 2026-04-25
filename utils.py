import textwrap

MAX_MSG_LEN = 133
# Overhead per chunk when split: "..." prefix (3) + "... [xx/xx]" suffix (11)
SPLIT_OVERHEAD = 14


def split_message(text):
    chunks = textwrap.wrap(text, width=MAX_MSG_LEN)
    if len(chunks) == 1:
        return chunks

    chunks = textwrap.wrap(text, width=MAX_MSG_LEN - SPLIT_OVERHEAD)
    n = len(chunks)
    result = []
    for i, chunk in enumerate(chunks):
        label = f"[{i + 1}/{n}]"
        if i == 0:
            result.append(f"{chunk}... {label}")
        elif i == n - 1:
            result.append(f"...{chunk} {label}")
        else:
            result.append(f"...{chunk}... {label}")
    return result


def node_label(contact):
    name = contact.get("adv_name", "") if contact else "unknown"
    key = (contact.get("public_key", "") if contact else "unknown")[:12]
    return f"{name} ({key})" if name else f"({key})"
