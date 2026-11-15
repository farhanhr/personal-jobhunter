"""Deterministic Indonesia location and remote-scope classification.

This module is pure: it never performs geocoding, network access, or inference
beyond a curated list of Indonesian places and country names. When the evidence is
insufficient the result is ``review`` rather than an optimistic ``eligible``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from personal_job_finder_api.domain.text import contains_phrase, normalize_words


class LocationStatus(StrEnum):
    """Whether a location may enter the main feed."""

    ELIGIBLE = "eligible"
    REVIEW = "review"
    EXCLUDED = "excluded"


class WorkMode(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class RemoteScope(StrEnum):
    INDONESIA_ONLY = "indonesia_only"
    WORLDWIDE = "worldwide"
    COUNTRY_RESTRICTED = "country_restricted"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class LocationClassification:
    """Normalized location values kept next to the untouched source text."""

    location_raw: str | None
    country: str | None
    province: str | None
    city: str | None
    status: LocationStatus
    work_mode: WorkMode
    remote_scope: RemoteScope
    reason: str


# Curated city -> province pairs. Only well-known places are listed so an
# unrecognized string cannot be silently promoted to an Indonesian location.
_INDONESIAN_CITIES: tuple[tuple[str, str, str], ...] = (
    ("tangerang selatan", "Tangerang Selatan", "Banten"),
    ("bandar lampung", "Bandar Lampung", "Lampung"),
    ("pangkal pinang", "Pangkal Pinang", "Kepulauan Bangka Belitung"),
    ("pematang siantar", "Pematang Siantar", "Sumatera Utara"),
    ("tebing tinggi", "Tebing Tinggi", "Sumatera Utara"),
    ("tanjung pinang", "Tanjung Pinang", "Kepulauan Riau"),
    ("gunung kidul", "Gunung Kidul", "DI Yogyakarta"),
    ("balikpapan", "Balikpapan", "Kalimantan Timur"),
    ("banjarbaru", "Banjarbaru", "Kalimantan Selatan"),
    ("banjarmasin", "Banjarmasin", "Kalimantan Selatan"),
    ("palangkaraya", "Palangkaraya", "Kalimantan Tengah"),
    ("samarinda", "Samarinda", "Kalimantan Timur"),
    ("pontianak", "Pontianak", "Kalimantan Barat"),
    ("surabaya", "Surabaya", "Jawa Timur"),
    ("yogyakarta", "Yogyakarta", "DI Yogyakarta"),
    ("tangerang", "Tangerang", "Banten"),
    ("pekanbaru", "Pekanbaru", "Riau"),
    ("palembang", "Palembang", "Sumatera Selatan"),
    ("semarang", "Semarang", "Jawa Tengah"),
    ("surakarta", "Surakarta", "Jawa Tengah"),
    ("sidoarjo", "Sidoarjo", "Jawa Timur"),
    ("cirebon", "Cirebon", "Jawa Barat"),
    ("cikarang", "Cikarang", "Jawa Barat"),
    ("karawang", "Karawang", "Jawa Barat"),
    ("makassar", "Makassar", "Sulawesi Selatan"),
    ("denpasar", "Denpasar", "Bali"),
    ("mataram", "Mataram", "Nusa Tenggara Barat"),
    ("kupang", "Kupang", "Nusa Tenggara Timur"),
    ("manado", "Manado", "Sulawesi Utara"),
    ("jayapura", "Jayapura", "Papua"),
    ("bandung", "Bandung", "Jawa Barat"),
    ("jakarta", "Jakarta", "DKI Jakarta"),
    ("bekasi", "Bekasi", "Jawa Barat"),
    ("depok", "Depok", "Jawa Barat"),
    ("bogor", "Bogor", "Jawa Barat"),
    ("malang", "Malang", "Jawa Timur"),
    ("batam", "Batam", "Kepulauan Riau"),
    ("padang", "Padang", "Sumatera Barat"),
    ("medan", "Medan", "Sumatera Utara"),
    ("serang", "Serang", "Banten"),
    ("ambon", "Ambon", "Maluku"),
    ("jogja", "Yogyakarta", "DI Yogyakarta"),
    ("yogya", "Yogyakarta", "DI Yogyakarta"),
    ("solo", "Surakarta", "Jawa Tengah"),
)

# Province aliases, including common English spellings used on job boards.
_INDONESIAN_PROVINCES: tuple[tuple[str, str], ...] = (
    ("kepulauan bangka belitung", "Kepulauan Bangka Belitung"),
    ("bangka belitung", "Kepulauan Bangka Belitung"),
    ("daerah istimewa yogyakarta", "DI Yogyakarta"),
    ("di yogyakarta", "DI Yogyakarta"),
    ("nusa tenggara barat", "Nusa Tenggara Barat"),
    ("nusa tenggara timur", "Nusa Tenggara Timur"),
    ("west nusa tenggara", "Nusa Tenggara Barat"),
    ("east nusa tenggara", "Nusa Tenggara Timur"),
    ("kalimantan barat", "Kalimantan Barat"),
    ("kalimantan tengah", "Kalimantan Tengah"),
    ("kalimantan selatan", "Kalimantan Selatan"),
    ("kalimantan timur", "Kalimantan Timur"),
    ("kalimantan utara", "Kalimantan Utara"),
    ("west kalimantan", "Kalimantan Barat"),
    ("central kalimantan", "Kalimantan Tengah"),
    ("south kalimantan", "Kalimantan Selatan"),
    ("east kalimantan", "Kalimantan Timur"),
    ("north kalimantan", "Kalimantan Utara"),
    ("sulawesi utara", "Sulawesi Utara"),
    ("sulawesi tengah", "Sulawesi Tengah"),
    ("sulawesi selatan", "Sulawesi Selatan"),
    ("sulawesi tenggara", "Sulawesi Tenggara"),
    ("sulawesi barat", "Sulawesi Barat"),
    ("north sulawesi", "Sulawesi Utara"),
    ("central sulawesi", "Sulawesi Tengah"),
    ("south sulawesi", "Sulawesi Selatan"),
    ("southeast sulawesi", "Sulawesi Tenggara"),
    ("west sulawesi", "Sulawesi Barat"),
    ("sumatera utara", "Sumatera Utara"),
    ("sumatera barat", "Sumatera Barat"),
    ("sumatera selatan", "Sumatera Selatan"),
    ("north sumatra", "Sumatera Utara"),
    ("west sumatra", "Sumatera Barat"),
    ("south sumatra", "Sumatera Selatan"),
    ("kepulauan riau", "Kepulauan Riau"),
    ("riau islands", "Kepulauan Riau"),
    ("jawa barat", "Jawa Barat"),
    ("jawa tengah", "Jawa Tengah"),
    ("jawa timur", "Jawa Timur"),
    ("west java", "Jawa Barat"),
    ("central java", "Jawa Tengah"),
    ("east java", "Jawa Timur"),
    ("dki jakarta", "DKI Jakarta"),
    ("jakarta raya", "DKI Jakarta"),
    ("maluku utara", "Maluku Utara"),
    ("papua barat", "Papua Barat"),
    ("west papua", "Papua Barat"),
    ("north maluku", "Maluku Utara"),
    ("banten", "Banten"),
    ("lampung", "Lampung"),
    ("bengkulu", "Bengkulu"),
    ("gorontalo", "Gorontalo"),
    ("jambi", "Jambi"),
    ("aceh", "Aceh"),
    ("riau", "Riau"),
    ("bali", "Bali"),
    ("maluku", "Maluku"),
    ("papua", "Papua"),
)

# Country names that positively place a role outside Indonesia. Longest phrases
# are matched first so "united states" cannot be read as a shorter token.
_FOREIGN_COUNTRIES: tuple[tuple[str, str], ...] = (
    ("united arab emirates", "United Arab Emirates"),
    ("united states of america", "United States"),
    ("united kingdom", "United Kingdom"),
    ("united states", "United States"),
    ("saudi arabia", "Saudi Arabia"),
    ("south korea", "South Korea"),
    ("south africa", "South Africa"),
    ("new zealand", "New Zealand"),
    ("czech republic", "Czech Republic"),
    ("hong kong", "Hong Kong"),
    ("sri lanka", "Sri Lanka"),
    ("timor leste", "Timor-Leste"),
    ("abu dhabi", "United Arab Emirates"),
    ("singapore", "Singapore"),
    ("malaysia", "Malaysia"),
    ("thailand", "Thailand"),
    ("vietnam", "Vietnam"),
    ("philippines", "Philippines"),
    ("australia", "Australia"),
    ("japan", "Japan"),
    ("china", "China"),
    ("taiwan", "Taiwan"),
    ("canada", "Canada"),
    ("germany", "Germany"),
    ("netherlands", "Netherlands"),
    ("france", "France"),
    ("spain", "Spain"),
    ("ireland", "Ireland"),
    ("poland", "Poland"),
    ("portugal", "Portugal"),
    ("sweden", "Sweden"),
    ("norway", "Norway"),
    ("denmark", "Denmark"),
    ("finland", "Finland"),
    ("switzerland", "Switzerland"),
    ("austria", "Austria"),
    ("italy", "Italy"),
    ("belgium", "Belgium"),
    ("romania", "Romania"),
    ("ukraine", "Ukraine"),
    ("turkey", "Turkey"),
    ("israel", "Israel"),
    ("india", "India"),
    ("dubai", "United Arab Emirates"),
    ("qatar", "Qatar"),
    ("egypt", "Egypt"),
    ("kenya", "Kenya"),
    ("nigeria", "Nigeria"),
    ("brazil", "Brazil"),
    ("mexico", "Mexico"),
    ("argentina", "Argentina"),
    ("chile", "Chile"),
    ("colombia", "Colombia"),
    ("pakistan", "Pakistan"),
    ("bangladesh", "Bangladesh"),
    ("nepal", "Nepal"),
    ("cambodia", "Cambodia"),
    ("laos", "Laos"),
    ("myanmar", "Myanmar"),
    ("brunei", "Brunei"),
    ("usa", "United States"),
    ("uae", "United Arab Emirates"),
    ("uk", "United Kingdom"),
)

_INDONESIA_REGIONS = ("jabodetabek", "jabodebek", "nusantara")

_REMOTE_MARKERS = (
    "remote",
    "work from home",
    "work-from-home",
    "wfh",
    "telecommute",
    "telecommuting",
    "distributed team",
    "anywhere",
)

_WORLDWIDE_MARKERS = (
    "worldwide",
    "world wide",
    "anywhere",
    "global",
    "globally",
    "international",
    "multiple countries",
    "fully remote",
)

_HYBRID_MARKERS = ("hybrid", "partially remote", "part remote")

_MISSING_LOCATION_VALUES = frozenset(
    {"", "-", "n/a", "na", "none", "null", "unknown", "unspecified", "not specified"}
)

_CITY_LOOKUP = sorted(_INDONESIAN_CITIES, key=lambda item: -len(item[0].split()))
_PROVINCE_LOOKUP = sorted(_INDONESIAN_PROVINCES, key=lambda item: -len(item[0].split()))
_COUNTRY_LOOKUP = sorted(_FOREIGN_COUNTRIES, key=lambda item: -len(item[0].split()))


def classify_location(raw_location: str | None) -> LocationClassification:
    """Classify a source location string without guessing missing evidence."""

    if raw_location is not None and not isinstance(raw_location, str):
        raise TypeError("raw_location must be a string or None")

    cleaned = (raw_location or "").strip()
    location_raw = cleaned or None
    if cleaned.casefold() in _MISSING_LOCATION_VALUES:
        return _unknown_location(location_raw, "The source did not provide a location.")

    text = normalize_words(cleaned)

    remote = _contains_any(text, _REMOTE_MARKERS)
    hybrid = _contains_any(text, _HYBRID_MARKERS)
    worldwide = _contains_any(text, _WORLDWIDE_MARKERS)

    city, city_province = _match_indonesian_city(text)
    province = _match_indonesian_province(text) or city_province
    in_indonesia = (
        "indonesia" in text
        or "indonesian" in text
        or city is not None
        or province is not None
        or _contains_any(text, _INDONESIA_REGIONS)
    )
    foreign_country = _match_foreign_country(text)

    if remote:
        if in_indonesia:
            return LocationClassification(
                location_raw=location_raw,
                country="Indonesia",
                province=province,
                city=city,
                status=LocationStatus.ELIGIBLE,
                work_mode=WorkMode.REMOTE,
                remote_scope=RemoteScope.INDONESIA_ONLY,
                reason="The source states a remote role open to Indonesia.",
            )
        if foreign_country is not None:
            return LocationClassification(
                location_raw=location_raw,
                country=foreign_country,
                province=None,
                city=None,
                status=LocationStatus.EXCLUDED,
                work_mode=WorkMode.REMOTE,
                remote_scope=RemoteScope.COUNTRY_RESTRICTED,
                reason="The source restricts this remote role to another country.",
            )
        if worldwide:
            return LocationClassification(
                location_raw=location_raw,
                country=None,
                province=None,
                city=None,
                status=LocationStatus.REVIEW,
                work_mode=WorkMode.REMOTE,
                remote_scope=RemoteScope.WORLDWIDE,
                reason=(
                    "The source describes a worldwide remote role. Worldwide remote "
                    "eligibility requires an explicit remote setting and is kept for "
                    "review."
                ),
            )
        return LocationClassification(
            location_raw=location_raw,
            country=None,
            province=None,
            city=None,
            status=LocationStatus.REVIEW,
            work_mode=WorkMode.REMOTE,
            remote_scope=RemoteScope.UNKNOWN,
            reason="The remote scope is not stated, so Indonesia eligibility is unproven.",
        )

    if in_indonesia:
        return LocationClassification(
            location_raw=location_raw,
            country="Indonesia",
            province=province,
            city=city,
            status=LocationStatus.ELIGIBLE,
            work_mode=WorkMode.HYBRID if hybrid else WorkMode.ONSITE,
            remote_scope=RemoteScope.NOT_APPLICABLE,
            reason="The location is in Indonesia.",
        )

    if foreign_country is not None:
        return LocationClassification(
            location_raw=location_raw,
            country=foreign_country,
            province=None,
            city=None,
            status=LocationStatus.EXCLUDED,
            work_mode=WorkMode.HYBRID if hybrid else WorkMode.ONSITE,
            remote_scope=RemoteScope.NOT_APPLICABLE,
            reason="The location is outside Indonesia.",
        )

    return LocationClassification(
        location_raw=location_raw,
        country=None,
        province=None,
        city=None,
        status=LocationStatus.REVIEW,
        work_mode=WorkMode.HYBRID if hybrid else WorkMode.UNKNOWN,
        remote_scope=RemoteScope.NOT_APPLICABLE,
        reason="The location could not be confirmed as being in Indonesia.",
    )


def _unknown_location(
    location_raw: str | None,
    reason: str,
) -> LocationClassification:
    return LocationClassification(
        location_raw=location_raw,
        country=None,
        province=None,
        city=None,
        status=LocationStatus.REVIEW,
        work_mode=WorkMode.UNKNOWN,
        remote_scope=RemoteScope.UNKNOWN,
        reason=reason,
    )


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(contains_phrase(text, phrase) for phrase in phrases)


def _match_indonesian_city(text: str) -> tuple[str | None, str | None]:
    for phrase, city, province in _CITY_LOOKUP:
        if contains_phrase(text, phrase):
            return city, province
    return None, None


def _match_indonesian_province(text: str) -> str | None:
    for phrase, province in _PROVINCE_LOOKUP:
        if contains_phrase(text, phrase):
            return province
    return None


def _match_foreign_country(text: str) -> str | None:
    for phrase, country in _COUNTRY_LOOKUP:
        if contains_phrase(text, phrase):
            return country
    return None
