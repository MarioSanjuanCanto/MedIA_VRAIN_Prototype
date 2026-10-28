"""Tests for speaker identity resolution and media_speakers tool."""

import pytest
from media_core.services.speaker_identities import (
    resolve_speaker_identities,
    get_speaker_at_timestamp,
    get_speaker_turns,
)
from media_core.tools.speakers import media_speakers


@pytest.fixture
def mock_parliament_segments():
    return [
        {
            "start": "00:00:01", "end": "00:00:05", "start_seconds": 1.0, "end_seconds": 5.0,
            "speaker": "SPEAKER_01", "text": "Buenos días. Comienza la sesión plenaria.",
        },
        {
            "start": "00:03:54", "end": "00:04:00", "start_seconds": 234.0, "end_seconds": 240.0,
            "speaker": "SPEAKER_01", "text": "Tiene la palabra la diputada señora Mónica Sales de la Cruz.",
        },
        {
            "start": "00:04:01", "end": "00:06:15", "start_seconds": 241.0, "end_seconds": 375.0,
            "speaker": "SPEAKER_07", "text": "Gracias, presidente. Presidente, mientras usted se pelea por los presupuestos...",
        },
        {
            "start": "00:06:18", "end": "00:06:24", "start_seconds": 378.0, "end_seconds": 384.0,
            "speaker": "SPEAKER_01", "text": "Para responder, tiene la palabra el presidente de la Generalitat.",
        },
        {
            "start": "00:06:25", "end": "00:08:03", "start_seconds": 385.0, "end_seconds": 483.0,
            "speaker": "SPEAKER_02", "text": "Gracias, señor presidente. Señora Sales, el Gobierno dice no a esta guerra.",
        },
        {
            "start": "00:18:56", "end": "00:19:05", "start_seconds": 1136.0, "end_seconds": 1145.0,
            "speaker": "SPEAKER_01", "text": "La siguiente pregunta es sobre la situación política y, en nombre de Vox, tiene la palabra el diputado Ignacio Garriga.",
        },
        {
            "start": "00:19:06", "end": "00:20:45", "start_seconds": 1146.0, "end_seconds": 1245.0,
            "speaker": "SPEAKER_00", "text": "Señor Illa, usted ha anunciado un plan contra el chabolismo. Más de un millón de catalanes están en pobreza extrema.",
        },
    ]


def test_resolve_speaker_identities(mock_parliament_segments):
    identities = resolve_speaker_identities(mock_parliament_segments, video_id="TEST_PARLIAMENT")

    assert "SPEAKER_01" in identities
    assert "Josep Rull" in identities["SPEAKER_01"]["name"]
    assert "Moderador" in identities["SPEAKER_01"]["role"]

    assert "SPEAKER_07" in identities
    assert identities["SPEAKER_07"]["name"] == "Mònica Sales"

    assert "SPEAKER_02" in identities
    assert identities["SPEAKER_02"]["name"] == "Salvador Illa"
    assert "President de la Generalitat" in identities["SPEAKER_02"]["role"]

    assert "SPEAKER_00" in identities
    assert identities["SPEAKER_00"]["name"] == "Ignacio Garriga"
    assert identities["SPEAKER_00"]["party"] == "VOX"


def test_get_speaker_at_timestamp(mock_parliament_segments, monkeypatch):
    monkeypatch.setattr(
        "media_core.services.speaker_identities.load_test_vector",
        lambda vid: {"segments": mock_parliament_segments},
    )

    # Test at 20:00 (1200 seconds) -> should be Ignacio Garriga (SPEAKER_00)
    info = get_speaker_at_timestamp("TEST_PARLIAMENT", 1200.0, segments=mock_parliament_segments)
    assert info["status"] == "success"
    assert info["speaker_id"] == "SPEAKER_00"
    assert info["name"] == "Ignacio Garriga"
    assert info["party"] == "VOX"
    assert "Diputado VOX" in info["display_name"]


def test_get_speaker_turns_by_name_and_role(mock_parliament_segments, monkeypatch):
    monkeypatch.setattr(
        "media_core.services.speaker_identities.load_test_vector",
        lambda vid: {"segments": mock_parliament_segments},
    )

    # By explicit name
    turns_illa = get_speaker_turns("TEST_PARLIAMENT", "Salvador Illa", segments=mock_parliament_segments)
    assert len(turns_illa) == 1
    assert turns_illa[0]["speaker_id"] == "SPEAKER_02"
    assert turns_illa[0]["name"] == "Salvador Illa"
    assert turns_illa[0]["start"] == "00:06:25"

    # By role alias "president"
    turns_pres = get_speaker_turns("TEST_PARLIAMENT", "president", segments=mock_parliament_segments)
    assert len(turns_pres) == 1
    assert turns_pres[0]["speaker_id"] == "SPEAKER_02"

    # By party "VOX"
    turns_vox = get_speaker_turns("TEST_PARLIAMENT", "VOX", segments=mock_parliament_segments)
    assert len(turns_vox) == 1
    assert turns_vox[0]["speaker_id"] == "SPEAKER_00"
    assert turns_vox[0]["name"] == "Ignacio Garriga"


def test_media_speakers_tool(mock_parliament_segments, monkeypatch, tmp_path):
    import media_core.tools.speakers as spk_tool
    monkeypatch.setattr(spk_tool, "resolve_video_file", lambda vid: None)
    monkeypatch.setattr(spk_tool, "load_test_vector", lambda vid: {"segments": mock_parliament_segments})

    # Test query by timestamp
    res_at_t = media_speakers(video_id="TEST_VID", timestamp=1200.0)
    assert res_at_t["status"] == "success"
    assert res_at_t["name"] == "Ignacio Garriga"

    # Test query all turns for person
    res_person = media_speakers(video_id="TEST_VID", person="Salvador Illa")
    assert res_person["status"] == "success"
    assert res_person["turns_count"] == 1

    # Test full directory
    res_all = media_speakers(video_id="TEST_VID")
    assert res_all["status"] == "success"
    assert res_all["speakers_count"] >= 3
