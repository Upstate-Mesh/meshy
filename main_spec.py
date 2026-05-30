from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from adsb import (
    AdsbService,
    _cardinal,
    _climb_indicator,
    _distance_bearing,
    _format_aircraft,
)
from db import NodeDB
from hf import HfService, _format
from nixle import NixleService
from utils import MAX_MSG_BYTES, SPLIT_OVERHEAD, node_label, split_message

NIXLE_HTML = """
<ol id="wire">
  <li id="pub_1">
    <div class="wire_content">
      <p class="time">Entered: 1 hour ago</p>
      <p>Road closure on Main St <a href="https://nixle.us/AAA11">More&nbsp;&raquo;</a></p>
    </div>
  </li>
  <li id="pub_2">
    <div class="wire_content">
      <p class="time">Entered: 3 hours ago</p>
      <p>Water main break on Elm Ave <a href="https://nixle.us/BBB22">More&nbsp;&raquo;</a></p>
    </div>
  </li>
  <li id="pub_3">
    <div class="wire_content">
      <p class="time">Entered: 5 hours ago</p>
      <p>Power outage on Oak St <a href="https://nixle.us/CCC33">More&nbsp;&raquo;</a></p>
    </div>
  </li>
</ol>
"""


def _mock_nixle(html=NIXLE_HTML):
    mock = MagicMock()
    mock.text = html
    mock.raise_for_status = MagicMock()
    return mock


def _make_nixle():
    return NixleService({"nixle": {"url": "http://localhost/nixle"}})


def test_short_message_returned_as_single_chunk():
    result = split_message("hello world")
    assert result == ["hello world"]


def test_exact_max_length_returned_as_single_chunk():
    text = "x" * MAX_MSG_BYTES
    result = split_message(text)
    assert result == [text]


def test_long_message_splits_into_multiple_chunks():
    text = ("word " * 40).strip()
    result = split_message(text)
    assert len(result) > 1


def test_all_chunks_within_max_length():
    text = ("word " * 40).strip()
    for chunk in split_message(text):
        assert len(chunk) <= MAX_MSG_BYTES


def test_first_chunk_ends_with_label():
    text = ("word " * 40).strip()
    result = split_message(text)
    assert result[0].endswith(f"[1/{len(result)}]")


def test_last_chunk_ends_with_label():
    text = ("word " * 40).strip()
    result = split_message(text)
    n = len(result)
    assert result[-1].endswith(f"[{n}/{n}]")


def test_middle_chunks_end_with_label():
    text = ("word " * 80).strip()
    result = split_message(text)
    assert len(result) >= 3
    for i, chunk in enumerate(result[1:-1], start=2):
        assert chunk.endswith(f"[{i}/{len(result)}]")


def test_counter_label_reflects_total():
    text = ("word " * 40).strip()
    result = split_message(text)
    n = len(result)
    for i, chunk in enumerate(result):
        assert f"[{i + 1}/{n}]" in chunk


def test_empty_string_returns_empty_list():
    assert not split_message("")


def test_single_word_at_max_length_not_split():
    text = "x" * MAX_MSG_BYTES
    result = split_message(text)
    assert len(result) == 1


def test_two_chunk_split_structure():
    inner_width = MAX_MSG_BYTES - SPLIT_OVERHEAD
    text = "word " * (inner_width // 5 + 5)
    result = split_message(text.strip())
    assert len(result) == 2
    assert result[0].endswith("[1/2]")
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


def test_format_alert():
    alert = {"id": "AAA11", "text": "Road closure on Main St"}
    assert (
        NixleService.format_alert(alert)
        == "Road closure on Main St https://nixle.us/AAA11"
    )


def test_get_alerts_returns_all_items():
    service = _make_nixle()
    with patch("nixle.requests.get", return_value=_mock_nixle()):
        alerts = service.get_alerts()
    assert len(alerts) == 3


def test_get_alerts_extracts_id_and_text():
    service = _make_nixle()
    with patch("nixle.requests.get", return_value=_mock_nixle()):
        alerts = service.get_alerts()
    assert alerts[0] == {"id": "AAA11", "text": "Road closure on Main St"}
    assert alerts[1] == {"id": "BBB22", "text": "Water main break on Elm Ave"}


def test_get_alerts_excludes_more_link_text():
    service = _make_nixle()
    with patch("nixle.requests.get", return_value=_mock_nixle()):
        alerts = service.get_alerts()
    for alert in alerts:
        assert "More" not in alert["text"]


def test_get_alerts_no_wire_returns_empty():
    service = _make_nixle()
    with patch(
        "nixle.requests.get", return_value=_mock_nixle("<html><body></body></html>")
    ):
        assert service.get_alerts() == []


def test_get_alerts_skips_items_without_nixle_link():
    html = """
    <ol id="wire">
      <li><p>No link here</p></li>
      <li><p>Has link <a href="https://nixle.us/DDD44">More</a></p></li>
    </ol>
    """
    service = _make_nixle()
    with patch("nixle.requests.get", return_value=_mock_nixle(html)):
        alerts = service.get_alerts()
    assert len(alerts) == 1
    assert alerts[0]["id"] == "DDD44"


def test_db_upsert_alert_new_returns_true(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    assert db.upsert_alert("AAA11") is True


def test_db_upsert_alert_duplicate_returns_false(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    db.upsert_alert("AAA11")
    assert db.upsert_alert("AAA11") is False


def test_db_upsert_alert_different_ids_both_stored(tmp_path):
    db = NodeDB(db_file=str(tmp_path / "test.db"))
    assert db.upsert_alert("AAA11") is True
    assert db.upsert_alert("BBB22") is True


HF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<solar><solardata>
  <calculatedconditions>
    <band name="80m-40m" time="day">Fair</band>
    <band name="30m-20m" time="day">Good</band>
    <band name="17m-15m" time="day">Good</band>
    <band name="12m-10m" time="day">Poor</band>
    <band name="80m-40m" time="night">Good</band>
    <band name="30m-20m" time="night">Good</band>
    <band name="17m-15m" time="night">Fair</band>
    <band name="12m-10m" time="night">Poor</band>
  </calculatedconditions>
</solardata></solar>"""


def _mock_hf(xml=HF_XML):
    mock = MagicMock()
    mock.text = xml
    mock.raise_for_status = MagicMock()
    return mock


def test_hf_format_day():
    assert (
        _format([("80m-40m", "Fair"), ("30m-20m", "Good")], "☀️")
        == "HF ☀️ 80m-40m:Fair 30m-20m:Good"
    )


def test_hf_format_night():
    assert _format([("80m-40m", "Good")], "🌙") == "HF 🌙 80m-40m:Good"


def test_hf_get_conditions_returns_two_messages():
    with patch("hf.requests.get", return_value=_mock_hf()):
        day_msg, night_msg = HfService().get_conditions()
    assert day_msg.startswith("HF ☀️")
    assert night_msg.startswith("HF 🌙")


def test_hf_get_conditions_day_bands():
    with patch("hf.requests.get", return_value=_mock_hf()):
        day_msg, _ = HfService().get_conditions()
    assert "80m-40m:Fair" in day_msg
    assert "30m-20m:Good" in day_msg
    assert "12m-10m:Poor" in day_msg


def test_hf_get_conditions_night_bands():
    with patch("hf.requests.get", return_value=_mock_hf()):
        _, night_msg = HfService().get_conditions()
    assert "80m-40m:Good" in night_msg
    assert "17m-15m:Fair" in night_msg
    assert "12m-10m:Poor" in night_msg


def test_hf_day_excludes_night_bands():
    with patch("hf.requests.get", return_value=_mock_hf()):
        day_msg, night_msg = HfService().get_conditions()
    assert "🌙" not in day_msg
    assert "☀️" not in night_msg


# --- Cooldown tests ---


def _make_meshy_stub():
    """Minimal Meshy-like object for testing _check_and_update_cooldown."""
    from main import Meshy

    with patch("main.Meshy.__init__", return_value=None):
        m = Meshy.__new__(Meshy)
    m._channel_cooldowns = {}
    m._check_and_update_cooldown = Meshy._check_and_update_cooldown.__get__(m)
    return m


def _parse_channel_cmd(text):
    """Mirror of the sender-prefix stripping in on_channel_receive."""
    cmd_text = text.strip()
    if ": " in cmd_text:
        cmd_text = cmd_text.split(": ", 1)[1]
    return cmd_text.strip().lower()


def test_channel_cmd_strips_sender_prefix():
    assert _parse_channel_cmd("KD2NSP: .aircraft") == ".aircraft"


def test_channel_cmd_no_prefix_passthrough():
    assert _parse_channel_cmd(".aircraft") == ".aircraft"


def test_channel_cmd_lowercases():
    assert _parse_channel_cmd("KD2NSP: .AIRCRAFT") == ".aircraft"


def test_channel_cmd_colon_in_message_body():
    # Only the first ": " is treated as sender separator
    assert _parse_channel_cmd("KD2NSP: say: hello") == "say: hello"


def test_cooldown_allows_first_call():
    m = _make_meshy_stub()
    assert m._check_and_update_cooldown("#adsb", ".seen", 300) is True


def test_cooldown_blocks_within_window():
    m = _make_meshy_stub()
    m._check_and_update_cooldown("#adsb", ".seen", 300)
    assert m._check_and_update_cooldown("#adsb", ".seen", 300) is False


def test_cooldown_allows_after_window():
    m = _make_meshy_stub()
    m._channel_cooldowns[("#adsb", ".seen")] = datetime.now(UTC) - timedelta(
        seconds=301
    )
    assert m._check_and_update_cooldown("#adsb", ".seen", 300) is True


def test_cooldown_independent_per_channel():
    m = _make_meshy_stub()
    m._check_and_update_cooldown("#adsb", ".seen", 300)
    assert m._check_and_update_cooldown("#ham", ".seen", 300) is True


def test_cooldown_independent_per_command():
    m = _make_meshy_stub()
    m._check_and_update_cooldown("#adsb", ".seen", 300)
    assert m._check_and_update_cooldown("#adsb", ".aircraft", 300) is True


def test_cooldown_records_timestamp():
    m = _make_meshy_stub()
    before = datetime.now(UTC)
    m._check_and_update_cooldown("#adsb", ".seen", 300)
    after = datetime.now(UTC)
    recorded = m._channel_cooldowns[("#adsb", ".seen")]
    assert before <= recorded <= after
