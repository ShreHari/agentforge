"""Remove contact details from text before we save it or show it to the AI.

The agent needs your skills and experience, not your email, phone number or profile
links. What we never store can never leak ("data minimisation", a UK GDPR principle).
Postal addresses are harder to spot reliably and are not removed yet.
"""

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Web links: with http(s) or www, plus common profile sites written without them.
LINK = re.compile(
    r"https?://\S+|www\.\S+"
    r"|\b(?:linkedin\.com|github\.com|gitlab\.com|twitter\.com|x\.com)/\S+"
    r"|\b[\w-]+\.github\.io\S*",
    re.IGNORECASE,
)
# A run of digits, spaces, dashes, dots and brackets, optionally starting with +.
PHONE_CANDIDATE = re.compile(r"\+?\(?\d[\d\s().-]{7,}\d")


def _phone_or_not(match: re.Match) -> str:
    # 9 to 15 digits is a phone number. Fewer is a date range like "2021 - 2025" (8).
    digits = sum(ch.isdigit() for ch in match.group())
    return "[phone]" if 9 <= digits <= 15 else match.group()


def redact_contact_details(text: str) -> str:
    text = EMAIL.sub("[email]", text)  # first, so an email isn't mistaken for a link
    text = LINK.sub("[link]", text)
    return PHONE_CANDIDATE.sub(_phone_or_not, text)
