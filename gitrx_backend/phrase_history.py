"""Literal observations in captured SPL-version order, never addition/removal dates."""
import base64
import hashlib
import json
import unicodedata

POLICY = 'gitrx-native-phrase-history-1'
MAX_VERSIONS = 10000


def _extraction(reader, sid, version):
    evidence = reader.evidence(sid, version) if hasattr(reader, 'evidence') else {}
    return evidence.get('extraction', {'status': 'unknown', 'diagnostics': []})


def _binding(reader, sid, phrase, code, ignore_case):
    return {'policy': POLICY, 'snapshot': reader.snapshot, 'setid': sid,
            'phrase': phrase, 'code': code, 'ignore_case': ignore_case,
            'evidence_metadata_sha256': getattr(reader, 'evidence_metadata_sha256', None),
            'unicode_version': unicodedata.unidata_version}


def _binding_hash(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _cursor(binding, offset):
    payload = json.dumps({'binding':_binding_hash(binding),'offset':offset}, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(payload).decode()


def _offset(cursor, binding):
    try:
        if not isinstance(cursor, str) or len(cursor) > 6000:
            raise ValueError()
        state = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
        if not isinstance(state, dict):
            raise ValueError()
        offset = state.pop('offset')
        if state != {'binding':_binding_hash(binding)} or type(offset) is not int or offset < 0:
            raise ValueError()
        return offset
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError('Invalid phrase-history cursor or query/release/metadata mismatch') from None


def _match(text, needle, ignore_case):
    index = (text.casefold() if ignore_case else text).find(needle)
    if index < 0:
        return None
    if ignore_case:
        folded = 0
        for original, char in enumerate(text):
            if folded + len(char.casefold()) > index:
                index = original
                break
            folded += len(char.casefold())
    start = max(0, index - 120)
    return {'match_start': index, 'excerpt_offset': start, 'text_excerpt': text[start:start + 500]}


def history(query, sid, phrase, code=None, ignore_case=True, limit=50, offset=0, cursor=None):
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 500:
        raise ValueError('Provide a nonempty literal phrase of at most 500 characters')
    if type(ignore_case) is not bool or type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError('Invalid phrase-history case or pagination bounds')
    if code is not None and (not isinstance(code, str) or not code or len(code) > 500):
        raise ValueError('Invalid source code selector')
    sid = query._sid(sid)
    binding = _binding(query.reader, sid, phrase, code, ignore_case)
    if cursor is not None:
        if offset:
            raise ValueError('Choose a cursor or offset, not both')
        offset = _offset(cursor, binding)
    versions = query.reader.versions(sid)
    if len(versions) > MAX_VERSIONS:
        raise ValueError('Family exceeds the supported 10000-version history bound')
    end = min(offset + limit, len(versions))
    needle = phrase.casefold() if ignore_case else phrase
    observations, previous, seen_present = [], None, False
    # Recompute prefix so cursors cannot fabricate a predecessor or earlier presence.
    # The enclosing Query deadline bounds this work; no partial answer on timeout.
    for i, version in enumerate(versions[:end]):
        number = version['version']
        sections, citation = query.reader.read(sid, number)
        extraction = _extraction(query.reader, sid, number)
        matches, count = [], 0
        for section in sections:
            if code is not None and section['loinc_code'] != code:
                continue
            match = _match(section.get('own_text') or '', needle, ignore_case)
            if match is None:
                continue
            count += 1
            if len(matches) < 20:
                matches.append(dict(match, ordinal=section['ordinal'], source_code=section.get('loinc_code'),
                                    code_system=section.get('code_system'), title=section.get('title'),
                                    ancestry=section.get('ancestry_path')))
        state = 'present' if count else ('checked_nonmatch' if extraction['status'] == 'complete' else 'unknown')
        gap = None
        if previous is not None and number != previous['version'] + 1:
            gap = {'after_version': previous['version'], 'before_version': number,
                   'unobserved_integer_count': max(0, number - previous['version'] - 1)}
        if previous is None:
            transition = {'present': 'present_at_record_start', 'checked_nonmatch': 'nonmatch_at_record_start',
                          'unknown': 'unknown_at_record_start'}[state]
        elif gap or state == 'unknown' or previous['evidence_state'] == 'unknown':
            transition = 'present_after_unknown' if state == 'present' else 'continuity_unknown'
        elif state == previous['evidence_state']:
            transition = 'continued_presence' if state == 'present' else 'continued_nonmatch'
        elif state == 'present':
            transition = 'observed_return' if seen_present else 'observed_nonmatch_to_match'
        else:
            transition = 'observed_match_to_nonmatch'
        row = {'version': number, 'publication_dates': version.get('publication_dates', []),
               'effective_date': version.get('effective_date'), 'citation': citation, 'extraction': extraction,
               'literal_presence': 'present' if count else 'not_observed_in_extracted_text',
               'evidence_state': state, 'transition': transition, 'gap_from_previous': gap,
               'previous_captured_version': previous['version'] if previous else None,
               'previous_captured_citation': previous['citation'] if previous else None,
               'previously_observed': seen_present, 'matching_occurrences': count,
               'matches': matches, 'matches_truncated': count > len(matches)}
        if i >= offset:
            observations.append(row)
        seen_present = seen_present or state == 'present'
        previous = row
    runs = []
    for row in observations:
        if runs and runs[-1]['evidence_state'] == row['evidence_state'] and not row['gap_from_previous']:
            runs[-1]['through_version'] = row['version']
            runs[-1]['captured_versions'] += 1
        else:
            runs.append({'from_version': row['version'], 'through_version': row['version'],
                         'evidence_state': row['evidence_state'], 'captured_versions': 1,
                         'continues_from_previous_page': bool(not runs and offset and row['transition'] in
                                                              ('continued_presence', 'continued_nonmatch'))})
    partial = end < len(versions)
    return query.envelope(setid=sid, phrase=phrase, source_code=code, ignore_case=ignore_case,
        policy=POLICY, evidence_metadata_sha256=binding['evidence_metadata_sha256'],
        observations=observations, page_runs=runs, runs_are_page_fragments=True,
        total_captured_versions=len(versions), offset=offset, next_offset=end if partial else None,
        continuation=_cursor(binding, end) if partial else None, partial=partial,
        scope='Literal substring within each native own_text occurrence; any selected occurrence. No cross-occurrence concatenation or lineage inference.',
        meaning='Captured SPL-version order, not actual addition/removal dates. Checked nonmatches describe extracted text only; complete parser status is not proof of source fidelity. Unknown negatives and numeric gaps break continuity. Repeated occurrences stay separate.',
        matching_policy={'case': 'Unicode casefold' if ignore_case else 'case sensitive',
                         'normalization': 'none beyond requested casefold; punctuation and whitespace preserved',
                         'unicode_version': unicodedata.unidata_version})
