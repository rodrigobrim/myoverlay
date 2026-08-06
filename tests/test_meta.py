"""Metadata composers (`mt meta` + publish), publishes-dict migration."""

from media_tools.i18n import strings as i18n_strings
from media_tools.library import DayManifest
from media_tools.meta import (
    TitleContext,
    compose_description,
    compose_title,
    update_details,
)

CTX = TitleContext(track="KGV 111", date="2026-07-30", session=3, best_lap="0:53.41")
NO_LAP_CTX = TitleContext(track="KGV 111", date="2026-07-30", session=3, best_lap="-:--.--")


def test_compose_title_appends_localized_meta():
    en, pt = i18n_strings("en"), i18n_strings("pt")
    assert compose_title("My onboard", CTX, en) == "My onboard - KGV 111 30/07/26 - BL 0:53.41"
    assert compose_title("Meu vídeo", CTX, pt) == "Meu vídeo - KGV 111 30/07/26 - MV 0:53.41"
    # empty user text -> the auto meta alone (the default publish title)
    assert compose_title("", CTX, pt) == "KGV 111 30/07/26 - MV 0:53.41"


def test_compose_title_no_meta_and_missing_best_lap():
    t = i18n_strings("pt")
    assert compose_title("Meu vídeo", CTX, t, no_meta=True) == "Meu vídeo"
    # no best lap -> track/date only, never a dash to "-:--.--"
    assert compose_title("", NO_LAP_CTX, t) == "KGV 111 30/07/26"


def test_compose_description_appends_meta_block():
    t = i18n_strings("pt")
    out = compose_description("Minha descrição.", CTX, t)
    assert out == (
        "Minha descrição.\n\n"
        "Gravado em 2026-07-30 em KGV 111.\n"
        "Melhor volta: 0:53.41\n\n"
        "Vídeo criado e publicado automaticamente pelo MyOverlay: "
        "https://github.com/rodrigobrim/myoverlay"
    )
    assert compose_description("Minha descrição.", CTX, t, no_meta=True) == "Minha descrição."
    assert compose_description("", CTX, t).startswith("Gravado em 2026-07-30")


def test_manifest_migrates_legacy_publishes_list():
    """Pre-dict manifests carried publishes as a flat list, each record with a
    `file` field; loading regroups them into the dict keyed by that file."""
    legacy = """
    {
      "date": "2026-07-30",
      "publishes": [
        {"file": "out/a.mp4", "video_id": "v1", "url": "https://youtu.be/v1",
         "privacy": "private", "published_at": "2026-07-30T20:00:00Z"},
        {"file": "out/a.mp4", "video_id": "v2", "url": "https://youtu.be/v2",
         "privacy": "private", "published_at": "2026-07-30T21:00:00Z"},
        {"file": "out/b.mp4", "video_id": "v3", "url": "https://youtu.be/v3",
         "privacy": "unlisted", "published_at": "2026-07-30T22:00:00Z"}
      ]
    }
    """
    m = DayManifest.model_validate_json(legacy)
    assert set(m.publishes) == {"out/a.mp4", "out/b.mp4"}
    assert [r.video_id for r in m.publishes["out/a.mp4"]] == ["v1", "v2"]
    assert m.publishes["out/b.mp4"][0].privacy == "unlisted"
    # round-trips as the dict shape (and keeps records without title/description)
    again = DayManifest.model_validate_json(m.model_dump_json())
    assert [r.video_id for r in again.publishes["out/a.mp4"]] == ["v1", "v2"]


class FakeYouTube:
    """videos().list/update stub capturing the update body."""

    def __init__(self, snippet, status):
        self.snippet, self.status = snippet, status
        self.updated = None

    def videos(self):
        return self

    def list(self, part, id):
        self._resp = {"items": [{"snippet": dict(self.snippet), "status": dict(self.status)}]}
        return self

    def update(self, part, body):
        self.updated = (part, body)
        return self

    def execute(self):
        resp = getattr(self, "_resp", {})
        self._resp = {}
        return resp


def test_update_details_touches_only_requested_fields():
    yt = FakeYouTube(
        snippet={"title": "old", "description": "old desc", "categoryId": "17"},
        status={"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    )
    update_details("vid1", yt, title="new title")
    part, body = yt.updated
    assert part == "snippet,status"
    assert body["snippet"]["title"] == "new title"
    # untouched fields survive the round-trip (categoryId is mandatory)
    assert body["snippet"]["description"] == "old desc"
    assert body["snippet"]["categoryId"] == "17"
    assert body["status"]["privacyStatus"] == "private"

    update_details("vid1", yt, visibility="unlisted")
    _, body = yt.updated
    assert body["status"]["privacyStatus"] == "unlisted"
    assert body["snippet"]["title"] == "old"
