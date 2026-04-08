import pytest

from main import MAX_MSG_LEN, SPLIT_OVERHEAD, split_message


def test_short_message_returned_as_single_chunk():
    result = split_message("hello world")
    assert result == ["hello world"]


def test_exact_max_length_returned_as_single_chunk():
    text = "x" * MAX_MSG_LEN
    result = split_message(text)
    assert result == [text]


def test_long_message_splits_into_multiple_chunks():
    text = ("word " * 40).strip()
    result = split_message(text)
    assert len(result) > 1


def test_all_chunks_within_max_length():
    text = ("word " * 40).strip()
    for chunk in split_message(text):
        assert len(chunk) <= MAX_MSG_LEN


def test_first_chunk_has_ellipsis_suffix():
    text = ("word " * 40).strip()
    result = split_message(text)
    assert result[0].endswith("... [1/%d]" % len(result))


def test_last_chunk_has_ellipsis_prefix():
    text = ("word " * 40).strip()
    result = split_message(text)
    n = len(result)
    assert result[-1].startswith("...")
    assert result[-1].endswith(f"[{n}/{n}]")


def test_middle_chunks_have_ellipsis_both_sides():
    text = ("word " * 80).strip()
    result = split_message(text)
    assert len(result) >= 3
    for chunk in result[1:-1]:
        assert chunk.startswith("...")
        assert "..." in chunk[3:]


def test_counter_label_reflects_total():
    text = ("word " * 40).strip()
    result = split_message(text)
    n = len(result)
    for i, chunk in enumerate(result):
        assert f"[{i + 1}/{n}]" in chunk


def test_empty_string_returns_empty_list():
    assert split_message("") == []


def test_single_word_at_max_length_not_split():
    text = "x" * MAX_MSG_LEN
    result = split_message(text)
    assert len(result) == 1


def test_two_chunk_split_structure():
    inner_width = MAX_MSG_LEN - SPLIT_OVERHEAD
    text = "word " * (inner_width // 5 + 5)
    result = split_message(text.strip())
    assert len(result) == 2
    assert result[0].endswith("... [1/2]")
    assert result[1].startswith("...")
    assert result[1].endswith("[2/2]")
