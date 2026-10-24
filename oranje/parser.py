"""Deterministische herkenning van dagtotalen en bewonersblokken."""
import re
import unicodedata
from datetime import date, timedelta

DAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
DAY_RE = r"(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)"
DETAIL = re.compile(r"^\s*" + DAY_RE + r"\s+maaltijden\s+nodig\s+voor\s*:?\s*", re.I | re.M)
SUMMARY = re.compile(r"^\s*" + DAY_RE + r"\s*:\s*(\d+)\s+maaltijden?\s+voor\s+(?:de\s+)?bewoners\s+en\s+(\d+)\s+(?:voor\s+)?begeleiders?\s*,?\s*totaal\s*(\d+)", re.I | re.M)

def norm(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if c.isalnum())

def monday(value):
    try:
        day = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Kies een geldige maandagdatum.")
    if day.weekday() != 0:
        raise ValueError("De gekozen week moet op een maandag beginnen.")
    return day

def parse_mail(text, week, residents):
    start = monday(week)
    if not isinstance(text, str) or not text.strip() or len(text) > 150000:
        raise ValueError("Plak mailtekst van maximaal 150.000 tekens.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    # Reply quotes should not hide a day heading.
    text = re.sub(r"(?m)^\s*(?:>\s*)+", "", text)
    lookup = {}
    for person in residents:
        if not person["active"]:
            continue
        for name in [person["name"], *person["aliases"]]:
            lookup.setdefault(norm(name), set()).add(person["id"])
    # A repeated day in the same section begins a second quoted order.
    headers = list(DETAIL.finditer(text))
    groups, current, seen = [], [], set()
    for h in headers:
        day = h.group(1).lower()
        gap = text[current[-1].end():h.start()] if current else ""
        if current and (day in seen or re.search(r"(?mi)^\s*(?:Van|From|Onderwerp|Subject):", gap)):
            groups.append(current)
            current, seen = [], set()
        current.append(h)
        seen.add(day)
    if current:
        groups.append(current)
    if not groups:
        raise ValueError("Geen bewonersblok gevonden. Verwacht bijvoorbeeld ‘Dinsdag maaltijden nodig voor:’. Vul losse correcties in het maandoverzicht in.")
    blocks = []
    for index, group in enumerate(groups):
        section_start = groups[index - 1][-1].end() if index else 0
        summaries = {}
        for s in SUMMARY.finditer(text, section_start, group[0].start()):
            summaries[s.group(1).lower()] = tuple(map(int, s.groups()[1:]))
        result, block_warnings = [], []
        for h in group:
            next_heading = next((x.start() for x in headers if x.start() > h.start()), len(text))
            body = text[h.end():next_heading]
            # End a names section at the final +N, blank paragraph or mail footer.
            body = re.split(r"\n\s*\n|\n\s*(?:Met vriendelijke|Groeten|Heb je|Van:|From:|Onderwerp:|--)", body, maxsplit=1, flags=re.I)[0].strip()
            staff_match = re.search(r"\+\s*(\d+)\b", body)
            staff_list = int(staff_match.group(1)) if staff_match else None
            if staff_match:
                body = body[:staff_match.start()]
            tokens = [x.strip(" \t\n.;:-") for x in re.split(r",|;|\ben\b", body, flags=re.I) if x.strip(" \t\n.;:-")]
            ids, unresolved, warnings = [], [], []
            for token in tokens:
                matches = lookup.get(norm(token), set())
                if len(matches) == 1:
                    rid = next(iter(matches))
                    if rid in ids:
                        warnings.append(f"Dubbele naam in lijst: {token}.")
                    else:
                        ids.append(rid)
                else:
                    unresolved.append({"name": token, "reason": "Meerdere bewoners passen" if matches else "Onbekende naam"})
            day = h.group(1).lower()
            expected = summaries.get(day)
            if expected:
                count, staff, total = expected
                if count != len(tokens):
                    warnings.append(f"Samenvatting noemt {count} bewoners, namenlijst bevat {len(tokens)} namen.")
                if count + staff != total:
                    warnings.append(f"Dagtotalen kloppen niet: {count} + {staff} ≠ {total}.")
                if staff_list is not None and staff_list != staff:
                    warnings.append(f"Begeleiders verschillen: samenvatting {staff}, namenlijst {staff_list}.")
            else:
                staff = staff_list if staff_list is not None else 0
                warnings.append("Geen bijbehorende samenvatting gevonden; controleer aantallen.")
            if not tokens:
                warnings.append("De bewonerslijst is leeg.")
            if staff_list is None and not expected:
                warnings.append("Begeleidersaantal ontbreekt; vul het in.")
            result.append({"date": (start + timedelta(days=DAYS.index(day))).isoformat(), "day": day,
                           "resident_ids": ids, "unresolved": unresolved, "staff": staff,
                           "warnings": warnings, "expected": expected, "raw_names": body})
        missing = set(summaries) - {d["day"] for d in result}
        if missing:
            block_warnings.append("Samenvatting zonder bewonerslijst voor: " + ", ".join(sorted(missing)))
        blocks.append({"label": f"Bestelling {index + 1}", "days": result, "warnings": block_warnings})
    return blocks
