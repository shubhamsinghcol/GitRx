"""Documented literal-v1 normalization, shared by later search consumers."""
import re
import unicodedata

WHITE_SPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680" + "".join(chr(c) for c in range(0x2000, 0x200B)) + "\u2028\u2029\u202f\u205f\u3000"
SPACE_RE = re.compile("[" + re.escape(WHITE_SPACE) + "]+")
TRANSLATE = {ord("\u00ad"): None}
TRANSLATE.update({ord(c): "'" for c in "\u2018\u2019\u201a\u201b"})
TRANSLATE.update({ord(c): '"' for c in "\u201c\u201d\u201e\u201f"})
TRANSLATE.update({ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\ufe63\uff0d"})


def normalize(text, ignore_case=False):
    result = SPACE_RE.sub(" ", unicodedata.normalize("NFC", text).translate(TRANSLATE)).strip(" ")
    return unicodedata.normalize("NFC", result.casefold()) if ignore_case else result
