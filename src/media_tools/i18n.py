"""Overlay / publish localization.

The selected language affects ONLY user-facing video output: the delta
overlay labels and the default YouTube title/description templates. Config
files, CLI output and logs stay in English.

Language is set by top-level `language` in config.toml (default "en").
"""

from __future__ import annotations

LANGUAGES = ["en", "pt", "es", "ja", "ar", "fr", "it", "ru"]

# Keys:
#   current/previous/best : lap-time panel labels
#   no_telemetry          : placeholder under the map when no data
#   speed_delta/time_delta: ruler captions
#   lap_word              : suffix word for per-lap slices ("... - lap 3")
#   title_template/description_template: default YouTube templates
STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "current": "Current",
        "previous": "Previous",
        "best": "Best",
        "no_telemetry": "no telemetry",
        "speed_delta": "Speed delta",
        "time_delta": "Time delta",
        "lap_word": "lap",
        "title_template": "Karting {track} {date} - session {session} (best lap {best_lap})",
        "description_template": "Recorded {date} at {track}.\nBest lap: {best_lap}\n\nUploaded by media-tools.",
    },
    "pt": {
        "current": "Atual",
        "previous": "Anterior",
        "best": "Melhor",
        "no_telemetry": "sem telemetria",
        "speed_delta": "Delta de velocidade",
        "time_delta": "Delta de tempo",
        "lap_word": "volta",
        "title_template": "Kart {track} {date} - sessão {session} (melhor volta {best_lap})",
        "description_template": "Gravado em {date} em {track}.\nMelhor volta: {best_lap}\n\nEnviado pelo media-tools.",
    },
    "es": {
        "current": "Actual",
        "previous": "Anterior",
        "best": "Mejor",
        "no_telemetry": "sin telemetría",
        "speed_delta": "Delta de velocidad",
        "time_delta": "Delta de tiempo",
        "lap_word": "vuelta",
        "title_template": "Karting {track} {date} - sesión {session} (mejor vuelta {best_lap})",
        "description_template": "Grabado el {date} en {track}.\nMejor vuelta: {best_lap}\n\nSubido por media-tools.",
    },
    "ja": {
        "current": "現在",
        "previous": "前回",
        "best": "ベスト",
        "no_telemetry": "テレメトリーなし",
        "speed_delta": "速度差",
        "time_delta": "タイム差",
        "lap_word": "ラップ",
        "title_template": "カート {track} {date} - セッション {session}（ベストラップ {best_lap}）",
        "description_template": "{date} に {track} で撮影。\nベストラップ: {best_lap}\n\nmedia-tools によるアップロード。",
    },
    "ar": {
        "current": "الحالية",
        "previous": "السابقة",
        "best": "الأفضل",
        "no_telemetry": "بدون تيليمتري",
        "speed_delta": "فرق السرعة",
        "time_delta": "فرق الزمن",
        "lap_word": "لفة",
        "title_template": "كارتينغ {track} {date} - جلسة {session} (أفضل لفة {best_lap})",
        "description_template": "سُجل بتاريخ {date} في {track}.\nأفضل لفة: {best_lap}\n\nرُفع بواسطة media-tools.",
    },
    "fr": {
        "current": "Actuel",
        "previous": "Précédent",
        "best": "Meilleur",
        "no_telemetry": "pas de télémétrie",
        "speed_delta": "Delta de vitesse",
        "time_delta": "Delta de temps",
        "lap_word": "tour",
        "title_template": "Karting {track} {date} - session {session} (meilleur tour {best_lap})",
        "description_template": "Enregistré le {date} à {track}.\nMeilleur tour : {best_lap}\n\nPublié par media-tools.",
    },
    "it": {
        "current": "Attuale",
        "previous": "Precedente",
        "best": "Migliore",
        "no_telemetry": "senza telemetria",
        "speed_delta": "Delta velocità",
        "time_delta": "Delta tempo",
        "lap_word": "giro",
        "title_template": "Karting {track} {date} - sessione {session} (miglior giro {best_lap})",
        "description_template": "Registrato il {date} a {track}.\nMiglior giro: {best_lap}\n\nCaricato da media-tools.",
    },
    "ru": {
        "current": "Текущий",
        "previous": "Предыдущий",
        "best": "Лучший",
        "no_telemetry": "нет телеметрии",
        "speed_delta": "Дельта скорости",
        "time_delta": "Дельта времени",
        "lap_word": "круг",
        "title_template": "Картинг {track} {date} - сессия {session} (лучший круг {best_lap})",
        "description_template": "Записано {date}, трасса {track}.\nЛучший круг: {best_lap}\n\nЗагружено media-tools.",
    },
}

# Arial (the default overlay font) has no CJK glyphs; prepend fonts that do
# for languages Arial cannot render. Arabic/Cyrillic are covered by Arial.
FONT_CANDIDATES: dict[str, list[str]] = {
    "ja": [
        "C:/Windows/Fonts/YuGothB.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    ],
}


def strings(language: str) -> dict[str, str]:
    """Strings for a language, falling back to English for unknown codes."""
    return STRINGS.get(language, STRINGS["en"])


def shape_text(text: str, language: str) -> str:
    """Prepare text for PIL rendering.

    PIL draws codepoints left-to-right with no contextual shaping, which
    mangles Arabic. When the optional arabic_reshaper + python-bidi packages
    are present, reshape and reorder; otherwise return the text unchanged
    (legible but unjoined).
    """
    if language != "ar":
        return text
    # Latin-only fragments (numbers, placeholders) need no shaping.
    if not any("؀" <= ch <= "ۿ" for ch in text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return text
    return get_display(arabic_reshaper.reshape(text))
