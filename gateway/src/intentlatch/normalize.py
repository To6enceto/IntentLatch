import base64
import binascii
import re
import unicodedata
from urllib.parse import unquote

BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{12,}={0,2}")
URL_SAFE_TO_STANDARD = str.maketrans("-_", "+/")


def strip_format_characters(text: str) -> str:
    # Category Cf holds every zero-width and bidi control character; none is ASCII.
    if text.isascii():
        return text
    return "".join(char for char in text if char.isascii() or unicodedata.category(char) != "Cf")


def decode_base64(candidate: str) -> str | None:
    data = candidate.rstrip("=").translate(URL_SAFE_TO_STANDARD)
    if len(data) % 4 == 1:
        return None
    try:
        return base64.b64decode(data + "=" * (-len(data) % 4), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def normalized_views(text: str) -> list[str]:
    """The texts regex policies run on: the text without format characters, then its decoded forms."""
    stripped = strip_format_characters(text)
    views = [stripped]
    if "%" in stripped:
        views.append(strip_format_characters(unquote(stripped)))
    # Decoded forms sit beside the original, so decoding can never hide a match in it.
    decoded = [
        decode_base64(match.group()) for view in views for match in BASE64_RUN.finditer(view)
    ]
    views += [strip_format_characters(found) for found in decoded if found is not None]
    return list(dict.fromkeys(views))
