"""Verbalize digit spans without letting the directing model change their value."""

from decimal import Decimal
import re

from num2words import num2words


_DIGITS = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,:/-]\d+)*"
NUMBER = re.compile(
    rf"(?P<value>(?:(?<!\w)[−+-])?{_DIGITS})"
    r"(?P<unit>[ -]?летн[а-яё]+|\s*[%₽]|\s?р(?=\.|\s|$)|\s?(?:mins?|hrs?)\b|[hm](?![a-z])"
    r"|[dд](?![a-zа-яё])|в\d+|-(?:й|я|е|го|му|м|ую|ые|ым|ой)\b)?",
    re.IGNORECASE,
)
CASES = frozenset("ngdaip")
GENDERS = frozenset("mfn")
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_UKRAINIAN = re.compile(r"[іїєґ]", re.IGNORECASE)
_TURKISH = re.compile(r"[ğĞıİşŞüÜöÖçÇ]")
_MONTH = re.compile(r"^\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b")
_UNITS = {
    "%": ("процент", "процента", "процентов"),
    "₽": ("рубль", "рубля", "рублей"),
    "h": ("час", "часа", "часов"),
    "m": ("минута", "минуты", "минут"),
}


def number_spans(text):
    return list(NUMBER.finditer(text))


def latin_number_line(text):
    return bool(re.search(r"[A-Za-z]{2,}", text) and not re.search(r"[а-яёіїєґ]", text, re.IGNORECASE))


def _value(raw, unit):
    raw = re.sub(r"[ \u00a0\u202f]", "", raw).replace("−", "-")
    if unit in ("h", "m", "hr", "hrs", "min", "mins") and re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", raw):
        raw = raw.replace(",", "")
    return Decimal(raw.replace(",", "."))


def _inflect(value, *, language="ru", case="n", gender="m", ordinal=False, animate=False, plural=False):
    if ordinal and value != int(value):
        ordinal = False
    if language != "ru":
        return num2words(value, lang=language, to="ordinal" if ordinal else "cardinal")
    if ordinal:
        return num2words(value, lang="ru", case=case, gender=gender, animate=animate, plural=plural, to="ordinal")
    return num2words(value, lang="ru", case=case, gender=gender, animate=animate,
                     to="cardinal")


def _russian_unit(value, unit):
    forms = _UNITS[unit]
    if value != int(value):
        return forms[1]
    whole = abs(int(value))
    if whole % 100 not in (11, 12, 13, 14):
        if whole % 10 == 1:
            return forms[0]
        if 2 <= whole % 10 <= 4:
            return forms[1]
    return forms[2]


def spoken_number(match, text, decision):
    raw = match["value"]
    unit = (match["unit"] or "").strip().casefold()
    case = decision.get("case", "n")
    gender = decision.get("gender", "m")
    case = case if isinstance(case, str) and case in CASES else "n"
    gender = gender if isinstance(gender, str) and gender in GENDERS else "m"
    animate = decision.get("animate") is True
    before = text[:match.start()].casefold()
    after = text[match.end():].casefold()
    # Explicit markers and grammatical forms in the source take precedence over guesses.
    if re.search(r"\b(?:до|около|порядка|свыше|менее|более|от|без|из)\s*$", before):
        case = "g"
    elif (not before.strip() or re.search(r"\bу (?:вас|нас|меня|тебя)\s*$", before)
          or re.search(r"[—–:]\s*(?:\d+\s+час(?:а|ов)?\s+)?$", before)):
        case = "n"
    if re.search(r"^\s*(?:минут[аы]|недел[яи]|секунд[аы])\b", after):
        gender = "f"
    language = "ru"
    if _UKRAINIAN.search(text) and decision.get("language", "uk") != "ru":
        language = "uk"
    if not _CYRILLIC.search(text):
        if _TURKISH.search(text):
            language = "tr"
        elif unit in {"min", "mins", "hr", "hrs"} or decision.get("language") == "en" or latin_number_line(text):
            language = "en"
    if unit == "р":
        unit = "₽"
    if unit.startswith("в") and unit[1:].isdigit():
        return f"{_inflect(_value(raw, ''))} в {_inflect(int(unit[1:]), case='p', gender='n')}"
    if unit.lstrip("-").startswith("летн"):
        compound = _inflect(_value(raw, ""), case="g").split()
        prefixes = {"ста": "сто", "одного": "одно"}
        return "".join(prefixes.get(part, part) for part in compound) + unit.lstrip("-")
    if re.search(r"\bс\s+\d{1,2}\.\d{2}\s+до\s+\d{1,2}\.\d{2}\b", text.casefold()):
        clock = re.fullmatch(r"([01]?\d|2[0-3])\.([0-5]\d)", raw)
        if clock:
            hours, minutes = map(int, clock.groups())
            return (f"{_inflect(hours, case='g')} часов" if minutes == 0
                    else f"{_inflect(hours, case='g')} {_inflect(minutes, case='g')}")
    if parts := re.fullmatch(r"(\d+)([:/])(\d+)", raw):
        left, separator, right = parts.groups()
        if separator == "/" and re.search(r"\bдавление\b", text, re.IGNORECASE):
            join = " на "
        elif separator == "/":
            join = " дробь "
        elif re.search(r"\b(?:сч[её]т|сч[её]том)\b", text, re.IGNORECASE):
            join = " — "
        else:
            join = " ноль " if right.startswith("0") and int(right) else " "
        return join.join((_inflect(int(left), language=language), _inflect(int(right), language=language)))
    if parts := re.fullmatch(r"([+-]?\d+)-(\d+)", raw):
        left, right = map(int, parts.groups())
        if re.match(r"^\s*классов\b", after):
            return " — ".join(_inflect(number, ordinal=True, case="g", plural=True) for number in (left, right))
        if re.search(r"\b(?:от|для|с)\s*$", before):
            return " — ".join(_inflect(number, language=language, case="g") for number in (left, right))
        return f"от {_inflect(left, language=language, case='g')} до {_inflect(right, language=language, case='g')}"

    try:
        value = _value(raw, unit)
    except ArithmeticError:
        return " — ".join(_inflect(int(part), language=language) for part in re.findall(r"\d+", raw))
    if unit in _UNITS:
        if language == "en":
            singular, plural = {"h": ("hour", "hours"), "m": ("minute", "minutes"),
                                "%": ("percent", "percent"), "₽": ("ruble", "rubles")}[unit]
            return f"{_inflect(value, language='en')} {singular if value == 1 else plural}"
        return f"{_inflect(value, gender='f' if unit=='m' else 'm')} {_russian_unit(value,unit)}"
    if unit in {"min", "mins", "hr", "hrs"}:
        noun = "minute" if unit.startswith("min") else "hour"
        if language == "en":
            return f"{_inflect(value, language='en')} {noun if value == 1 else noun + 's'}"
        abbreviation = "m" if noun == "minute" else "h"
        return f"{_inflect(value,gender='f' if abbreviation=='m' else 'm')} {_russian_unit(value,abbreviation)}"
    if unit in {"d","д"}:
        return f"{_inflect(value)} дэ"
    ordinal = unit.startswith("-") or decision.get("kind") == "ordinal"
    if _MONTH.match(after):
        case, gender, ordinal = "g", "n", True
    elif re.match(r"^\s*года\b", after) and 1000 <= value <= 2999:
        case, gender, ordinal = "g", "m", True
    elif re.search(r"\bв\s*$", before) and re.match(r"^\s*классе\b", after):
        case, ordinal = "p", True
    elif re.search(r"\bв конце\s*$", before) and re.match(r"^\s*сезона\b", after):
        case, ordinal = "g", True
    if unit in {"-го","-му","-ым","-ой"}:
        case = {"-го":"g","-му":"d","-ым":"i","-ой":case}[unit]
    if unit in {"-я","-ую","-ой"}:
        gender = "f"
    if unit == "-е":
        gender = "n"
    if unit == "-м" and re.search(r"\b(?:о|об|в|на|при)\s*$", before):
        case = "p"
    return _inflect(value, language=language, case=case, gender=gender, ordinal=ordinal, animate=animate)


def expand_numbers(text, decisions):
    """Return (spoken text, audited replacements). LLM decisions are positional and bounded."""
    written = []
    edits = []
    previous = 0
    for index, match in enumerate(number_spans(text), 1):
        value = spoken_number(match, text, decisions.get(index, {}))
        if match.start() and text[match.start()-1].isalpha():
            value = " " + value
        if match.end()<len(text) and text[match.end()].isalpha():
            value += " "
        written.extend((text[previous:match.start()], value))
        edits.append({"source": match.group(), "spoken": value.strip()})
        previous = match.end()
    written.append(text[previous:])
    return "".join(written), edits
