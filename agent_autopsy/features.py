"""Offline text features: tokenization, TF-cosine similarity, and the
regex pattern banks used by the failure-mode detectors. Pure stdlib."""
import math
import re
from collections import Counter

STOPWORDS = frozenset("""
a an the and or but if then else for of to in on at by with from as is are was
were be been being it its this that these those i you he she we they them his
her our your their my me him us do does did done have has had having will would
can could should may might must shall not no yes so such than too very just
also into over under after before between during about above below up down out
off again once here there when where which who whom whose what why how all any
both each few more most other some only own same than s t re ve ll don isn
aren wasn weren hasn haven didn doesn won wouldn couldn shouldn
""".split())

WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-_]{1,}")


def tokens(text):
    return [w for w in WORD_RE.findall((text or "").lower())
            if w not in STOPWORDS]


def tf_vector(text):
    return Counter(tokens(text))


def cosine(c1, c2):
    if not c1 or not c2:
        return 0.0
    dot = sum(v * c2.get(k, 0) for k, v in c1.items())
    n1 = math.sqrt(sum(v * v for v in c1.values()))
    n2 = math.sqrt(sum(v * v for v in c2.values()))
    return dot / (n1 * n2) if n1 and n2 else 0.0


# --- pattern banks -----------------------------------------------------------

ERROR_PATTERNS = [
    r"traceback \(most recent call last\)",
    r"\b(error|exception|failed|failure)\b[:\s]",
    r"\b(404|403|401|500|502|503|504)\b",
    r"\btimeout\b|\btimed out\b",
    r"\brate.?limit",
    r"\bconnection (refused|reset|aborted)\b",
    r"\bnot found\b|\bno such (file|tool|command)\b",
    r"\bpermission denied\b",
    r"\bout of memory\b|\bCUDA out of memory\b",
    r"\bsyntaxerror\b|\btypeerror\b|\bvalueerror\b|\bkeyerror\b",
    r"\bcommand not found\b",
]
ERROR_RE = re.compile("|".join(ERROR_PATTERNS), re.I)

TRANSIENT_PATTERNS = [
    r"\btimeout\b|\btimed out\b",
    r"\brate.?limit\w*\b",
    r"\b(connection (refused|reset|aborted)|network (error|unreachable))\b",
    r"\b(502|503|504)\b.*\b(gateway|service|timeout)\b",
    r"\btemporar\w+ (unavailable|failure)\b",
    r"\bplease retry\b",
]
TRANSIENT_RE = re.compile("|".join(TRANSIENT_PATTERNS), re.I)

INJECTION_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"disregard (all )?(previous|above) instructions",
    r"new (system )?instructions?:",
    r"system:\s*you (are|must|should)",
    r"override (the|your) (previous |prior )?instructions",
    r"do not (follow|obey) (the|your) (previous|original) instructions",
    r"instead, (you must|do|send|reveal|output)",
    r"jailbreak|prompt injection",
]
INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.I)

COMPLETION_CLAIMS = [
    r"\b(done|finished|completed|all done)\b[.!]?\s*$",
    r"\btask (is )?complete[.!]?\s*$",
    r"\bsuccessfully (completed|finished|done)\b",
]
COMPLETION_RE = re.compile("|".join(COMPLETION_CLAIMS), re.I | re.M)

NAN_PATTERNS = [r"\bnan\b", r"\binf\b.*loss", r"loss[=:]\s*nan"]
NAN_RE = re.compile("|".join(NAN_PATTERNS), re.I)


def has_error(text):
    return bool(ERROR_RE.search(text or ""))


def is_transient(text):
    return bool(TRANSIENT_RE.search(text or ""))


def has_injection(text):
    return bool(INJECTION_RE.search(text or ""))
