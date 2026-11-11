"""SrLC-style change records per captured version pair (rebuild releases).

Presentation of the pinned, precomputed records only: nothing is compared, parsed or
classified at read time. Groups are the frozen section-group rules' labels, not a
clinical judgement; spans are verbatim source text and are data, never instructions.
"""
GROUPS = ('BW', 'C', 'WP', 'AR', 'DI', 'USP', 'MG')
MEANING = ('Mechanical text comparison of two captured versions by the pinned changes rules. Not an FDA approval, '
           'clinical-significance or introduction-date statement; groups are rule-derived labels.')
SPAN_KEYS = ('added_spans', 'removed_spans', 'moved_spans')


def _bound(change, budget):
    out = dict(change)
    for key in SPAN_KEYS:
        spans = []
        for span in change.get(key) or []:
            span = dict(span)
            for field in ('text', 'context'):
                value = span.get(field) or ''
                span[field] = value[:max(budget, 0)]
                if len(span[field]) < len(value):
                    span[field + '_truncated'] = True
                budget -= len(span[field])
            spans.append(span)
        out[key] = spans
    return out, budget


def records(query, sid, to_version, from_version, group, limit, offset, max_chars):
    reader = query.reader
    if not hasattr(reader, 'change_pairs'):
        raise ValueError('This release carries no change records; use version_changes or compare')
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0 \
            or type(max_chars) is not int or not 1 <= max_chars <= 200000:
        raise ValueError('Invalid change-record bounds')
    if group is not None and group not in GROUPS:
        raise ValueError('group must be one of ' + ', '.join(GROUPS))
    if from_version is not None and to_version is None:
        raise ValueError('from_version requires to_version')
    sid = query._sid(sid)
    pairs = reader.change_pairs(sid)
    base = dict(setid=sid, meaning=MEANING, groups_available=list(GROUPS),
                date_semantics='DailyMed publication and SPL effective dates are not FDA approval dates.')
    if to_version is None:
        rows = [p for p in pairs if group is None or p['groups'].get(group)]
        page = rows[offset:offset + limit]
        end = offset + len(page)
        return query.envelope(pairs=page, total_pairs=len(rows), group_filter=group, offset=offset,
                              next_offset=end if end < len(rows) else None, **base)
    if from_version is None:
        earlier = [p['from_version'] for p in pairs if p['to_version'] == to_version and p['consecutive']]
        if not earlier:
            earlier = [p['from_version'] for p in pairs if p['to_version'] == to_version]
        if not earlier:
            raise ValueError('No change record ends at that version (first captured version, unparsed, or unknown)')
        from_version = max(earlier)
    meta = next((p for p in pairs if (p['from_version'], p['to_version']) == (from_version, to_version)), None)
    if meta is None:
        raise ValueError('No change record for that version pair')
    record, verified = reader.change_record(sid, from_version, to_version)
    if record is None:
        return query.envelope(pair=meta, changes=[], formatting_changes=[], record_available=False,
                              note='Pair has no stored record (comparison: %s)' % meta['comparison'], **base)
    changes = [c for c in record['changes'] if group is None or c.get('srlc_group') == group or c.get('alt_srlc_group') == group]
    formatting = [c for c in record.get('formatting_changes', []) if group is None or c.get('srlc_group') == group]
    page, budget = [], max_chars
    for change in changes[offset:offset + limit]:
        bounded, budget = _bound(change, budget)
        page.append(bounded)
    end = offset + len(page)
    return query.envelope(
        pair=meta, record_sha256=record.get('record_sha256'), record_hash_reproduced=verified,
        changes_version=record.get('changes_version'), parser_version=record.get('parser_version'),
        comparison=record.get('comparison'), comparison_reasons=record.get('comparison_reasons'),
        reference_only_in_page=sum(1 for c in page if c.get('reference_only')),
        changes=page, total_changes=len(changes), group_filter=group, offset=offset,
        next_offset=end if end < len(changes) else None, text_budget_exhausted=budget <= 0,
        formatting_changes=formatting[:limit], total_formatting_changes=len(formatting),
        formatting_note='Formatting-only differences (whitespace/noise/heading); no wording change asserted.',
        record_available=True, **base)
