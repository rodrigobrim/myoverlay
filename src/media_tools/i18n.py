"""Overlay / publish localization.

The selected language affects ONLY user-facing video output: the delta
overlay labels and the default YouTube title/description templates. Config
files, CLI output and logs stay in English.

Language is set by top-level `language` in config.toml (default "en").
"""

from __future__ import annotations

LANGUAGES = ["en", "pt", "es", "ja", "ar", "fr", "it", "ru", "zh"]

# Keys:
#   current/previous/best : lap-time panel labels
#   no_telemetry          : placeholder under the map when no data
#   speed_delta/time_delta: ruler captions
#   lap_word              : suffix word for per-lap slices ("... - lap 3")
#   best_lap_abbrev       : best-lap abbreviation in the auto title meta
#                           ("KGV 111 30/07/26 - MV 0:53.41")
#   description_template  : the auto description meta (recorded-at, best lap,
#                           MyOverlay credit), shared by publish and `mt meta`
_URL = "https://github.com/rodrigobrim/myoverlay"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "current": "Current",
        "previous": "Previous",
        "best": "Best",
        "no_telemetry": "no telemetry",
        "speed_delta": "Speed delta",
        "time_delta": "Time delta",
        "lap_word": "lap",
        "best_lap_abbrev": "BL",
        "description_template": "Recorded {date} at {track}.\nBest lap: {best_lap}\n\n"
        f"Video created and published automatically by MyOverlay: {_URL}",
    },
    "pt": {
        "current": "Atual",
        "previous": "Anterior",
        "best": "Melhor",
        "no_telemetry": "sem telemetria",
        "speed_delta": "Delta de velocidade",
        "time_delta": "Delta de tempo",
        "lap_word": "volta",
        "best_lap_abbrev": "MV",
        "description_template": "Gravado em {date} em {track}.\nMelhor volta: {best_lap}\n\n"
        f"Vídeo criado e publicado automaticamente pelo MyOverlay: {_URL}",
    },
    "es": {
        "current": "Actual",
        "previous": "Anterior",
        "best": "Mejor",
        "no_telemetry": "sin telemetría",
        "speed_delta": "Delta de velocidad",
        "time_delta": "Delta de tiempo",
        "lap_word": "vuelta",
        "best_lap_abbrev": "MV",
        "description_template": "Grabado el {date} en {track}.\nMejor vuelta: {best_lap}\n\n"
        f"Vídeo creado y publicado automáticamente por MyOverlay: {_URL}",
    },
    "ja": {
        "current": "現在",
        "previous": "前回",
        "best": "ベスト",
        "no_telemetry": "テレメトリーなし",
        "speed_delta": "速度差",
        "time_delta": "タイム差",
        "lap_word": "ラップ",
        "best_lap_abbrev": "BL",
        "description_template": "{date} に {track} で撮影。\nベストラップ: {best_lap}\n\n"
        f"MyOverlay により自動作成・公開された動画: {_URL}",
    },
    "ar": {
        "current": "الحالية",
        "previous": "السابقة",
        "best": "الأفضل",
        "no_telemetry": "بدون تيليمتري",
        "speed_delta": "فرق السرعة",
        "time_delta": "فرق الزمن",
        "lap_word": "لفة",
        "best_lap_abbrev": "BL",
        "description_template": "سُجل بتاريخ {date} في {track}.\nأفضل لفة: {best_lap}\n\n"
        f"فيديو أُنشئ ونُشر تلقائيًا بواسطة MyOverlay: {_URL}",
    },
    "fr": {
        "current": "Actuel",
        "previous": "Précédent",
        "best": "Meilleur",
        "no_telemetry": "pas de télémétrie",
        "speed_delta": "Delta de vitesse",
        "time_delta": "Delta de temps",
        "lap_word": "tour",
        "best_lap_abbrev": "MT",
        "description_template": "Enregistré le {date} à {track}.\nMeilleur tour : {best_lap}\n\n"
        f"Vidéo créée et publiée automatiquement par MyOverlay : {_URL}",
    },
    "it": {
        "current": "Attuale",
        "previous": "Precedente",
        "best": "Migliore",
        "no_telemetry": "senza telemetria",
        "speed_delta": "Delta velocità",
        "time_delta": "Delta tempo",
        "lap_word": "giro",
        "best_lap_abbrev": "MG",
        "description_template": "Registrato il {date} a {track}.\nMiglior giro: {best_lap}\n\n"
        f"Video creato e pubblicato automaticamente da MyOverlay: {_URL}",
    },
    "ru": {
        "current": "Текущий",
        "previous": "Предыдущий",
        "best": "Лучший",
        "no_telemetry": "нет телеметрии",
        "speed_delta": "Дельта скорости",
        "time_delta": "Дельта времени",
        "lap_word": "круг",
        "best_lap_abbrev": "ЛК",
        "description_template": "Записано {date}, трасса {track}.\nЛучший круг: {best_lap}\n\n"
        f"Видео создано и опубликовано автоматически MyOverlay: {_URL}",
    },
    "zh": {
        "current": "当前",
        "previous": "上一圈",
        "best": "最佳",
        "no_telemetry": "无遥测数据",
        "speed_delta": "速度差",
        "time_delta": "时间差",
        "lap_word": "圈",
        "best_lap_abbrev": "BL",
        "description_template": "录制于 {date}，赛道 {track}。\n最佳单圈：{best_lap}\n\n"
        f"由 MyOverlay 自动创建并发布的视频：{_URL}",
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
    "zh": [
        "C:/Windows/Fonts/msyhbd.ttc",  # Microsoft YaHei Bold
        "C:/Windows/Fonts/msyh.ttc",    # Microsoft YaHei
        "C:/Windows/Fonts/simhei.ttf",  # SimHei
        "C:/Windows/Fonts/simsun.ttc",  # SimSun
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
