"""Review-only first-200-character triage. Never imported by query filtering."""
import re

from .normalize import normalize


def lint_preview(code,text):
    preview=normalize(text[:200])
    lower=preview.casefold();reasons=[]
    if not preview or not re.sub(r'\[(?:media|footnote)[^\]]*\]','',lower).strip():
        reasons.append('empty or reference-only preview; cannot assess prose')
    if code=='34066-1':
        if re.search(r'aerobic|microorganisms|staphylococcus|streptococcus|enterococcus',lower):
            reasons.append('organism-list vocabulary in a boxed-warning-coded preview')
        if not re.search(r'warn|risk|serious|fatal|death|avoid|contraindicat|life.threat|suicid|hepatotoxic|teratogen',lower):
            reasons.append('no screened warning/risk cue in the first 200 characters')
    elif code=='34070-3':
        if not re.search(r'contraindicat|must not|do not|should not|not (?:be )?(?:used|use)|hypersensitiv|none\b|pregnan|allerg',lower):
            reasons.append('no screened prohibition/contraindication cue in the first 200 characters')
        if re.search(r'aerobic|microorganisms|staphylococcus|streptococcus|enterococcus',lower):
            reasons.append('organism-list vocabulary in a contraindications-coded preview')
    return reasons
