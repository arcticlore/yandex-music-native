"""«Моя волна» settings: the only values the station API accepts.

The personal station is tuned through ``rotor_station_settings2``, which takes
three strings and rejects everything outside its own vocabulary with HTTP 400.
The GUI used to offer ``bright`` mood and ``diverse``/``strict``/``maximum``
diversity, none of which the server knows, so every attempt to apply the station
settings failed and the UI silently kept the old selection.

This module owns the complete, closed set of accepted values and converts them
to the wire format. The GUI builds its controls from :data:`WAVE_MOODS`,
:data:`WAVE_ACTIVITIES`, :data:`WAVE_LANGUAGES` and :data:`WAVE_DIVERSITIES`,
so an invalid value cannot be produced in the first place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WAVE_MOODS = ("all", "fun", "sad", "calm", "energetic", "dark")
WAVE_ACTIVITIES = ("all", "work", "rest", "run", "party", "sleep", "drive")
WAVE_LANGUAGES = ("all", "russian", "not-russian")
WAVE_DIVERSITIES = ("default", "favorite", "discover", "popular")

REJECTED_VALUES = ("bright", "diverse", "strict", "maximum")

DEFAULT_MOOD = "all"
DEFAULT_ACTIVITY = "all"
DEFAULT_LANGUAGE = "all"
DEFAULT_DIVERSITY = "default"

MOOD_ALIASES = {
    "": None,
    "all": None,
    "any": None,
    "fun": "fun",
    "happy": "fun",
    "joy": "fun",
    "sad": "sad",
    "dark": "dark",
    "calm": "calm",
    "relax": "calm",
    "energetic": "energetic",
    "active": "energetic",
    "energy": "energetic",
}
ACTIVITY_ALIASES = {
    "": None,
    "all": None,
    "any": None,
    "work": "work",
    "job": "work",
    "office": "work",
    "study": "work",
    "rest": "rest",
    "relax": "rest",
    "chill": "rest",
    "run": "run",
    "sport": "run",
    "workout": "run",
    "fitness": "run",
    "party": "party",
    "dance": "party",
    "sleep": "sleep",
    "bed": "sleep",
    "drive": "drive",
    "driving": "drive",
    "car": "drive",
}
LANGUAGE_ALIASES = {
    "": None,
    "all": None,
    "any": None,
    "world": None,
    "russian": "russian",
    "ru": "russian",
    "rus": "russian",
    "not-russian": "not-russian",
    "not_russian": "not-russian",
    "foreign": "not-russian",
    "en": "not-russian",
    "none": "not-russian",
    "world-music": "not-russian",
}
DIVERSITY_ALIASES = {
    "": None,
    "default": "default",
    "favorite": "favorite",
    "favourite": "favorite",
    "likes": "favorite",
    "discover": "discover",
    "new": "discover",
    "popular": "popular",
    "top": "popular",
}

MOOD_LABELS = {
    "all": "Любое настроение",
    "fun": "Весёлое",
    "sad": "Грустное",
    "calm": "Спокойное",
    "energetic": "Энергичное",
    "dark": "Мрачное",
}
ACTIVITY_LABELS = {
    "all": "Любая активность",
    "work": "Работа",
    "rest": "Отдых",
    "run": "Тренировка",
    "party": "Вечеринка",
    "sleep": "Сон",
    "drive": "За рулём",
}
LANGUAGE_LABELS = {
    "all": "Любой язык",
    "russian": "Только русский",
    "not-russian": "Без русского",
}
DIVERSITY_LABELS = {
    "default": "Стандарт",
    "favorite": "Похожее на моё",
    "discover": "Новое и интересное",
    "popular": "Популярное",
}

MOOD_TO_MOOD_ENERGY = {
    "all": "all",
    "fun": "fun",
    "sad": "sad",
    "calm": "calm",
    "energetic": "active",
    "dark": "sad",
}
ACTIVITY_TO_MOOD_ENERGY = {
    "all": None,
    "work": "calm",
    "rest": "calm",
    "run": "active",
    "party": "fun",
    "sleep": "calm",
    "drive": "active",
}
LEGACY_LANGUAGE_TO_WIRE = {"all": "any"}
WIRE_LANGUAGE = ("any", "russian", "not-russian")

LEGACY_MOOD_ALIASES = {0: "sad", 1: "fun"}
LEGACY_ENERGY_ALIASES = {0: "calm", 1: "active"}
LEGACY_MOOD_ENERGY_TO_MOOD = {
    "all": None,
    "fun": "fun",
    "active": "energetic",
    "calm": "calm",
    "sad": "sad",
}
LEGACY_ENERGY_TO_ACTIVITY = {
    "calm": "rest",
    "active": "run",
}


@dataclass(frozen=True)
class WaveSettings:
    """A validated station selection. ``None`` means the axis is left on ``all``."""

    mood: str | None = None
    activity: str | None = None
    language: str | None = None
    diversity: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "mood": self.mood or DEFAULT_MOOD,
            "activity": self.activity or DEFAULT_ACTIVITY,
            "language": self.language or DEFAULT_LANGUAGE,
            "diversity": self.diversity or DEFAULT_DIVERSITY,
        }

    @property
    def is_default(self) -> bool:
        return self.mood is None and self.activity is None

    @property
    def mood_energy(self) -> str:
        """Combined ``moodEnergy`` value understood by ``rotor_station_settings2``."""
        if self.activity is not None:
            mapped = ACTIVITY_TO_MOOD_ENERGY[self.activity]
            if mapped is not None:
                return mapped
        return MOOD_TO_MOOD_ENERGY[self.mood or DEFAULT_MOOD]

    @property
    def wire_language(self) -> str:
        value = self.language
        if value is None:
            return "any"
        return LEGACY_LANGUAGE_TO_WIRE.get(value, value)

    @property
    def wire_diversity(self) -> str:
        return self.diversity or DEFAULT_DIVERSITY

    def to_wire(self) -> tuple[str, str, str]:
        return (self.mood_energy, self.wire_diversity, self.wire_language)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


def _validate(
    value: Any,
    aliases: dict[str, str | None],
    allowed: tuple[str, ...],
    title: str,
) -> str | None:
    name = _text(value)
    if name in REJECTED_VALUES:
        raise ValueError(
            f"значение «{name}» сервер не принимает ({title}): " + ", ".join(allowed)
        )
    if name not in aliases:
        raise ValueError(f"неизвестное значение ({title}): " + ", ".join(allowed))
    return aliases[name]


def normalize_mood(value: Any) -> str | None:
    """Validate a mood, ``None`` for ``all``."""
    if isinstance(value, bool):
        raise ValueError("настроение принимает название, а не флаг")
    if isinstance(value, int):
        mapped = LEGACY_MOOD_ALIASES.get(value)
        if mapped is None:
            raise ValueError("настроение принимает 0 или 1 в устаревшем виде")
        return mapped
    return _validate(value, MOOD_ALIASES, WAVE_MOODS, "настроение")


def normalize_activity(value: Any) -> str | None:
    """Validate an activity, ``None`` for ``all``."""
    if isinstance(value, bool):
        raise ValueError("активность принимает название, а не флаг")
    if isinstance(value, int):
        energy = LEGACY_ENERGY_ALIASES.get(value)
        if energy is None:
            raise ValueError("активность принимает 0 или 1 в устаревшем виде")
        return LEGACY_ENERGY_TO_ACTIVITY[energy]
    return _validate(value, ACTIVITY_ALIASES, WAVE_ACTIVITIES, "занятие")


def normalize_language(value: Any) -> str | None:
    """Validate a language filter, ``None`` for ``all``."""
    return _validate(value, LANGUAGE_ALIASES, WAVE_LANGUAGES, "язык")


def normalize_diversity(value: Any) -> str | None:
    """Validate a discovery mode, ``None`` for ``default``."""
    return _validate(value, DIVERSITY_ALIASES, WAVE_DIVERSITIES, "режим подбора")


def wave_settings(
    mood: Any = None,
    activity: Any = None,
    language: Any = None,
    diversity: Any = None,
) -> WaveSettings:
    """Validate a full selection, raising ``ValueError`` on any unknown value."""
    return WaveSettings(
        mood=normalize_mood(mood),
        activity=normalize_activity(activity),
        language=normalize_language(language),
        diversity=normalize_diversity(diversity),
    )


def legacy_settings(
    mood: Any = None,
    energy: Any = None,
    mood_energy: Any = None,
) -> WaveSettings:
    """Translate the deprecated 0/1 ``mood``/``energy`` arguments."""
    explicit = _text(mood_energy)
    if explicit:
        if explicit not in LEGACY_MOOD_ENERGY_TO_MOOD:
            raise ValueError("неизвестное значение (настроение): " + ", ".join(WAVE_MOODS))
        return WaveSettings(mood=LEGACY_MOOD_ENERGY_TO_MOOD[explicit])
    result = WaveSettings()
    for value, aliases, axis in (
        (energy, LEGACY_ENERGY_ALIASES, "activity"),
        (mood, LEGACY_MOOD_ALIASES, "mood"),
    ):
        if value is None:
            continue
        if isinstance(value, bool):
            key = 1 if value else 0
        elif isinstance(value, int):
            key = value
        else:
            name = _text(value)
            if name in WAVE_MOODS or name in MOOD_ALIASES:
                result = WaveSettings(
                    mood=normalize_mood(name), activity=result.activity
                )
                continue
            if name in WAVE_ACTIVITIES or name in ACTIVITY_ALIASES:
                result = WaveSettings(
                    mood=result.mood, activity=normalize_activity(name)
                )
                continue
            raise ValueError("неизвестное значение (настроение): " + ", ".join(WAVE_MOODS))
        mapped = aliases.get(key)
        if mapped is None:
            raise ValueError("настроение/энергия принимают 0 или 1")
        if axis == "mood":
            result = WaveSettings(
                mood=LEGACY_MOOD_ENERGY_TO_MOOD[mapped],
                activity=result.activity,
            )
        else:
            result = WaveSettings(
                mood=result.mood, activity=LEGACY_ENERGY_TO_ACTIVITY[mapped]
            )
    return result


def resolve_settings(
    mood: Any = None,
    activity: Any = None,
    language: Any = None,
    diversity: Any = None,
    energy: Any = None,
    mood_energy: Any = None,
) -> WaveSettings:
    """Validate modern arguments, falling back to the deprecated 0/1 form."""
    if activity is None and energy is None and mood_energy is None:
        return wave_settings(
            mood=mood, activity=None, language=language, diversity=diversity
        )
    base = legacy_settings(mood=mood, energy=energy, mood_energy=mood_energy)
    return WaveSettings(
        mood=base.mood,
        activity=normalize_activity(activity) if activity is not None else base.activity,
        language=normalize_language(language) if language is not None else base.language,
        diversity=normalize_diversity(diversity)
        if diversity is not None
        else base.diversity,
    )


def chips(values: tuple[str, ...], labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """``(value, label)`` pairs for a chip group, in declaration order."""
    return tuple((value, labels[value]) for value in values)


__all__ = [
    "ACTIVITY_LABELS",
    "DEFAULT_ACTIVITY",
    "DEFAULT_DIVERSITY",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MOOD",
    "DIVERSITY_ALIASES",
    "DIVERSITY_LABELS",
    "LANGUAGE_ALIASES",
    "LANGUAGE_LABELS",
    "MOOD_ALIASES",
    "MOOD_LABELS",
    "REJECTED_VALUES",
    "WAVE_ACTIVITIES",
    "WAVE_DIVERSITIES",
    "WAVE_LANGUAGES",
    "WAVE_MOODS",
    "WaveSettings",
    "chips",
    "legacy_settings",
    "normalize_activity",
    "normalize_diversity",
    "normalize_language",
    "normalize_mood",
    "resolve_settings",
    "wave_settings",
]
