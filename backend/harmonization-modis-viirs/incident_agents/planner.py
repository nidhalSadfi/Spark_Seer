"""Turn a free-text query into a validated search plan.

The model proposes zones (name + ISO-2 country) and keywords per language. Code
resolves each zone to a box and checks everything; on validation errors the
errors go back to the model, at most MAX_RETRIES times. An empty search result
is a valid outcome and never triggers a retry.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .geo import COUNTRY_BOXES, Geocoder
from .llm import Groq

MAX_RETRIES = 2
MAX_ZONES = 5
MAX_BOX_DEG = 25.0  # wider zones make the source queries meaningless

LANGS_BY_COUNTRY = {"FR": ["fr"], "DZ": ["ar", "fr"], "TN": ["ar", "fr"], "MA": ["ar", "fr"],
                    "ES": ["es"], "PT": ["pt"], "IT": ["it"], "GR": ["el"], "TR": ["tr"],
                    "LB": ["ar", "fr"], "SY": ["ar"]}

SEED_TERMS = {
    "fr": ["incendie", "feu de forêt", "feux de forêt", "départ de feu", "pompiers"],
    "ar": ["حريق", "حرائق", "حريق غابة", "الحماية المدنية"],
    "en": ["wildfire", "forest fire", "bushfire"],
    "es": ["incendio forestal", "incendio"],
    "pt": ["incêndio", "incêndio florestal", "fogo"],
    "it": ["incendio boschivo", "incendio"],
    "el": ["πυρκαγιά", "φωτιά"],
    "tr": ["orman yangını", "yangın"],
}

SYSTEM = """You turn a request about fires into a search plan. Reply with JSON only.
- zones: the countries, regions, departments or towns the user wants searched. name = the zone as it is
  officially called (for a country use its English name, e.g. "France"), country = ISO 3166-1 alpha-2 code.
  kind = country | region | place. If the user names only a country, return that country.
- keywords: for each language people in those zones post in, 3-6 short search terms that someone reporting a
  vegetation or forest fire would write (no hashtags, no generic words like "fire" alone in English).
Do not add zones the user did not ask for."""

SCHEMA = {
    "type": "object",
    "properties": {
        "zones": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "country": {"type": "string"},
                           "kind": {"type": "string", "enum": ["country", "region", "place"]}},
            "required": ["name", "country", "kind"], "additionalProperties": False}},
        "keywords": {"type": "array", "items": {
            "type": "object",
            "properties": {"lang": {"type": "string"}, "terms": {"type": "array", "items": {"type": "string"}}},
            "required": ["lang", "terms"], "additionalProperties": False}},
    },
    "required": ["zones", "keywords"],
    "additionalProperties": False,
}


@dataclass
class Zone:
    name: str
    country: str
    kind: str
    box: tuple[float, float, float, float]
    label: str


@dataclass
class Plan:
    query: str
    hours: int
    zones: list[Zone]
    keywords: dict[str, list[str]]
    attempts: int = 1
    notes: list[str] = field(default_factory=list)

    @property
    def countries(self) -> list[str]:
        return sorted({z.country for z in self.zones})

    def to_dict(self) -> dict:
        return asdict(self)


class PlanError(ValueError):
    pass


def validate(raw: dict, geocoder: Geocoder) -> tuple[list[Zone], dict[str, list[str]], list[str]]:
    """Return (zones, keywords, errors). Errors are sentences the model can act on."""
    errors: list[str] = []
    zones: list[Zone] = []
    raw_zones = raw.get("zones") or []
    if not raw_zones:
        errors.append("no zones: return at least one country, region or town")
    if len(raw_zones) > MAX_ZONES:
        errors.append(f"too many zones ({len(raw_zones)}); keep at most {MAX_ZONES}")
    for z in raw_zones[:MAX_ZONES]:
        name, cc = (z.get("name") or "").strip(), (z.get("country") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", cc):
            errors.append(f"zone '{name}': country '{cc}' is not an ISO-2 code")
            continue
        if not name:
            errors.append("a zone has an empty name")
            continue
        if z.get("kind") == "country" and cc in COUNTRY_BOXES:
            # A country named in Arabic or French ("الجزائر") would go to Nominatim, which once
            # returned the capital city for "Algeria". The mainland table is the answer.
            from .geo import COUNTRY_NAMES
            zones.append(Zone(COUNTRY_NAMES.get(cc, name), cc, "country", COUNTRY_BOXES[cc], COUNTRY_NAMES.get(cc, name)))
            continue
        found = geocoder.zone_box(name, cc)
        if not found:
            errors.append(f"zone '{name}' was not found inside country {cc}; use its official name or the country")
            continue
        box, label = found
        if box[2] - box[0] > MAX_BOX_DEG or box[3] - box[1] > MAX_BOX_DEG:
            if cc in COUNTRY_BOXES:
                box, label = COUNTRY_BOXES[cc], label
            else:
                errors.append(f"zone '{name}' is too large; name a region inside {cc}")
                continue
        zones.append(Zone(name, cc, z.get("kind") or "region", box, label))
    keywords: dict[str, list[str]] = {}
    for k in raw.get("keywords") or []:
        lang = (k.get("lang") or "").strip().lower()[:2]
        terms = [t.strip() for t in k.get("terms") or [] if 1 < len(t.strip()) <= 40]
        if re.fullmatch(r"[a-z]{2}", lang) and terms:
            keywords.setdefault(lang, [])
            keywords[lang] += [t for t in terms if t not in keywords[lang]]
    return zones, keywords, errors


MODEL_TERMS_PER_LANG = 2   # model terms are extras; the reviewed seed terms carry the search
MAX_TERMS_PER_LANG = 6     # Bluesky makes one call per term


def finish_keywords(keywords: dict[str, list[str]], zones: list[Zone]) -> dict[str, list[str]]:
    """Search each zone's main languages with the seed terms first, plus at most two model terms.
    Languages without seed terms are dropped: the model has invented codes before ("ka" for Kabyle)."""
    out: dict[str, list[str]] = {}
    # Known countries: only their own languages (English would pull global noise and burn the token budget).
    langs = [l for z in zones for l in LANGS_BY_COUNTRY.get(z.country, [])]
    if any(z.country not in LANGS_BY_COUNTRY for z in zones):
        langs += list(keywords)
    for lang in dict.fromkeys(langs):
        if lang not in SEED_TERMS:
            continue
        extra = [t for t in keywords.get(lang, []) if len(t.split()) <= 3][:MODEL_TERMS_PER_LANG]
        terms = SEED_TERMS[lang][:4] + extra
        out[lang] = list(dict.fromkeys(terms))[:MAX_TERMS_PER_LANG]
    return {k: v for k, v in out.items() if v}


def make_plan(query: str, hours: int, llm: Groq, geocoder: Geocoder, report=None) -> Plan:
    report = report or (lambda *a, **k: None)
    user = f"Request: {query}"
    last_errors: list[str] = []
    for attempt in range(1, MAX_RETRIES + 2):
        report("plan", "running", f"asking the model for a search plan (attempt {attempt})")
        raw = llm.json(SYSTEM, user, SCHEMA, name="search_plan", max_tokens=1200, expect_tokens=1200)
        zones, keywords, errors = validate(raw, geocoder)
        final = attempt > MAX_RETRIES
        if zones and (not errors or final):
            plan = Plan(query, hours, zones, finish_keywords(keywords, zones), attempts=attempt, notes=errors)
            if errors:
                report("validate", "warning", "kept the valid zones; dropped: " + "; ".join(errors))
            report("plan", "ok", ", ".join(f"{z.label} ({z.country})" for z in zones),
                   data={"zones": [asdict(z) for z in zones], "keywords": plan.keywords})
            return plan
        last_errors = errors or ["no usable zone"]
        report("validate", "retry" if attempt <= MAX_RETRIES else "error", "; ".join(last_errors))
        user = (f"Request: {query}\nYour previous plan: {raw}\nIt failed these checks: "
                f"{'; '.join(last_errors)}\nReturn a corrected plan.")
    raise PlanError("; ".join(last_errors))
