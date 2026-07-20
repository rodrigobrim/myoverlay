"""Language support: i18n tables, config validation, overlay label wiring."""

import pytest
from pydantic import ValidationError

from media_tools import i18n
from media_tools.config import Config
from media_tools.overlay import OverlayRenderer


def test_all_languages_have_all_keys():
    keys = set(i18n.STRINGS["en"])
    for lang in i18n.LANGUAGES:
        assert lang in i18n.STRINGS
        assert set(i18n.STRINGS[lang]) == keys, lang


def test_strings_falls_back_to_english():
    assert i18n.strings("xx") == i18n.STRINGS["en"]


def test_config_accepts_and_normalizes_language():
    cfg = Config(library_root="X:/lib", language=" PT ")
    assert cfg.language == "pt"


def test_config_rejects_unknown_language():
    with pytest.raises(ValidationError):
        Config(library_root="X:/lib", language="klingon")


def test_config_language_defaults_to_english():
    assert Config(library_root="X:/lib").language == "en"


@pytest.mark.parametrize(
    "lang,current",
    [("en", "Current"), ("pt", "Atual"), ("it", "Attuale"), ("zh", "当前")],
)
def test_overlay_labels_follow_language(lang, current):
    r = OverlayRenderer(320, 180, language=lang)
    assert r._t["current"] == current


def test_shape_text_leaves_non_arabic_untouched():
    assert i18n.shape_text("Speed delta", "ar") == "Speed delta"
    assert i18n.shape_text("Atual", "pt") == "Atual"


def test_title_templates_have_valid_placeholders():
    values = {"track": "t", "date": "d", "session": 1, "best_lap": "1:00.00", "lap": 2}
    for lang in i18n.LANGUAGES:
        t = i18n.strings(lang)
        t["title_template"].format(**values)
        t["description_template"].format(**values)
