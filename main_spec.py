from unittest.mock import MagicMock, patch

from adsb import (
    AdsbService,
    _cardinal,
    _climb_indicator,
    _distance_bearing,
    _format_aircraft,
)
from db import NodeDB
from utils import MAX_MSG_LEN, SPLIT_OVERHEAD, node_label, split_message


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
    assert result[0].endswith(f"... [1/{len(result)}]")


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
    assert not split_message("")


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


def test_node_label_with_name_and_key():
    contact = {"adv_name": "KD2NSP", "public_key": "abcdef123456789000"}
    assert node_label(contact) == "KD2NSP (abcdef123456)"


def test_node_label_without_name_shows_key_only():
    contact = {"adv_name": "", "public_key": "abcdef123456789000"}
    assert node_label(contact) == "(abcdef123456)"


def test_node_label_truncates_key_to_12_chars():
    contact = {"adv_name": "Test", "public_key": "a" * 32}
    assert node_label(contact) == f"Test ({'a' * 12})"


def test_node_label_none_contact_returns_unknown():
    assert node_label(None) == "unknown (unknown)"


def _make_adsb(config=None):
    return AdsbService(
        config
        or {
            "adsb": {
                "url": "http://localhost:8080",
                "max_age_seconds": 60,
                "max_count": 5,
            }
        }
    )


def _mock_response(aircraft):
    mock = MagicMock()
    mock.json.return_value = {"aircraft": aircraft}
    return mock


def test_get_aircraft_no_recent_aircraft():
    service = _make_adsb()
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response([])
        assert service.get_aircraft() == "No aircraft currently seen."


def test_get_aircraft_filters_by_max_age():
    service = _make_adsb()
    aircraft = [
        {"hex": "aaa111", "flight": "AA100", "seen": 10},
        {"hex": "bbb222", "flight": "BB200", "seen": 90},  # too old
    ]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "[AA100]" in result
        assert "[BB200]" not in result


def test_get_aircraft_uses_hex_when_no_callsign():
    service = _make_adsb()
    aircraft = [{"hex": "abc123", "flight": "", "seen": 5}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "[ABC123]" in result


def test_get_aircraft_formats_low_altitude_as_feet():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "alt_baro": 5000}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "5000ft" in result


def test_get_aircraft_formats_high_altitude_as_flight_level():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "alt_baro": 35000}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "FL350" in result


def test_get_aircraft_omits_altitude_when_on_ground():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "alt_baro": "ground"}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "ft" not in result
        assert "FL" not in result


def test_get_aircraft_respects_max_count():
    service = _make_adsb(
        {
            "adsb": {
                "url": "http://localhost:8080",
                "max_age_seconds": 60,
                "max_count": 2,
            }
        }
    )
    aircraft = [
        {"hex": "aaa111", "flight": "AA1", "seen": 1},
        {"hex": "bbb222", "flight": "BB2", "seen": 2},
        {"hex": "ccc333", "flight": "CC3", "seen": 3},
    ]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "[AA1]" in result
        assert "[BB2]" in result
        assert "[CC3]" not in result


def test_get_aircraft_shows_on_ground():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "alt_baro": "ground"}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "🛬" in service.get_aircraft()


def test_get_aircraft_shows_category():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "category": "A7"}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "🚁" in service.get_aircraft()


def test_get_aircraft_omits_unknown_category():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "category": "A3"}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        result = service.get_aircraft()
        assert "🚁" not in result


def test_get_aircraft_shows_heading():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "track": 90}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "→E" in service.get_aircraft()


def test_get_aircraft_shows_climb():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "baro_rate": 1500}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "⬆️1500fpm" in service.get_aircraft()


def test_get_aircraft_shows_descent():
    service = _make_adsb()
    aircraft = [{"hex": "aaa111", "flight": "AA100", "seen": 5, "baro_rate": -800}]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "⬇️800fpm" in service.get_aircraft()


def test_get_aircraft_shows_distance_when_home_configured():
    service = _make_adsb(
        {
            "adsb": {
                "url": "http://localhost:8080",
                "max_age_seconds": 60,
                "max_count": 5,
                "home_lat": 42.65,
                "home_lon": -73.75,
            }
        }
    )
    aircraft = [
        {"hex": "aaa111", "flight": "AA100", "seen": 5, "lat": 42.65, "lon": -73.75}
    ]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "📍0.0mi" in service.get_aircraft()


def test_get_aircraft_no_distance_without_home():
    service = _make_adsb()
    aircraft = [
        {"hex": "aaa111", "flight": "AA100", "seen": 5, "lat": 42.65, "lon": -73.75}
    ]
    with patch("adsb.requests.get") as mock_get:
        mock_get.return_value = _mock_response(aircraft)
        assert "📍" not in service.get_aircraft()


def test_cardinal_directions():
    assert _cardinal(0) == "N"
    assert _cardinal(90) == "E"
    assert _cardinal(180) == "S"
    assert _cardinal(270) == "W"
    assert _cardinal(45) == "NE"
    assert _cardinal(315) == "NW"


def test_climb_indicator_climbing():
    assert _climb_indicator(1200) == "⬆️1200fpm"


def test_climb_indicator_descending():
    assert _climb_indicator(-800) == "⬇️800fpm"


def test_climb_indicator_level():
    assert _climb_indicator(50) == ""


def test_climb_indicator_none():
    assert _climb_indicator(None) == ""


def test_distance_bearing_same_point():
    distance, _ = _distance_bearing(42.65, -73.75, 42.65, -73.75)
    assert distance == 0.0


def test_distance_bearing_cardinal():
    _, bearing = _distance_bearing(0.0, 0.0, 0.0, 1.0)
    assert bearing == "E"


def test_format_aircraft_full():
    a = {
        "hex": "aaa111",
        "flight": "AA100",
        "alt_baro": 35000,
        "track": 270,
        "baro_rate": 1000,
        "category": "A5",
        "lat": 42.65,
        "lon": -73.75,
    }
    result = _format_aircraft(a, home_lat=42.65, home_lon=-73.75)
    assert "[AA100]" in result
    assert "Heavy" in result
    assert "FL350" in result
    assert "→W" in result
    assert "⬆️1000fpm" in result
    assert "📍0.0mi" in result


def test_db_inserts_new_node(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("abc123", "KD2NSP", 42.0, -73.0)
    nodes = db.get_seen_nodes()
    assert len(nodes) == 1
    assert nodes[0]["name"] == "KD2NSP"
    assert nodes[0]["lat"] == 42.0
    assert nodes[0]["lon"] == -73.0


def test_db_updates_name_on_change(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("abc123", "OldName")
    db.upsert_node("abc123", "NewName")
    nodes = db.get_seen_nodes()
    assert nodes[0]["name"] == "NewName"


def test_db_preserves_name_when_empty_update(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("abc123", "KD2NSP")
    db.upsert_node("abc123", "")
    nodes = db.get_seen_nodes()
    assert nodes[0]["name"] == "KD2NSP"


def test_db_preserves_lat_lon_when_none_update(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("abc123", "KD2NSP", 42.0, -73.0)
    db.upsert_node("abc123", "KD2NSP", None, None)
    nodes = db.get_seen_nodes()
    assert nodes[0]["lat"] == 42.0
    assert nodes[0]["lon"] == -73.0


def test_db_updates_lat_lon(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("abc123", "KD2NSP", 42.0, -73.0)
    db.upsert_node("abc123", "KD2NSP", 43.0, -74.0)
    nodes = db.get_seen_nodes()
    assert nodes[0]["lat"] == 43.0
    assert nodes[0]["lon"] == -74.0


def test_db_orders_by_most_recently_seen(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_node("aaa111", "Alpha")
    db.upsert_node("bbb222", "Beta")
    db.upsert_node("aaa111", "Alpha")  # touch again to make most recent
    nodes = db.get_seen_nodes()
    assert nodes[0]["id"] == "aaa111"
