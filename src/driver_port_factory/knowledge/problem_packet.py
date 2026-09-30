"""One-question, bounded evidence packets. No model, inferred mapping or acceptance gate."""
import hashlib
import json
import math
import re

from ..core.models import StageStatus, WorkflowError
from ..migration.contracts import MigrationArtifact as A, MigrationStage as S

TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_:.-]*')
STOP = set('a an the and or to of in on for with from is are be as by how what which '
           'should can does do this that these those please implement implementation '
           'driver code function target source use using need ensure handle'.split())
GROUPS = ('obligations', 'source', 'target', 'test')


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def terms(query):
    # Chinese questions remain visible, but do not turn individual characters into noisy hits.
    latin = list(dict.fromkeys(t for t in TOKEN.findall(query) if t.lower() not in STOP))
    return latin[:16] or ([query.strip()] if query.strip() else [])


def pattern(term):
    return re.compile(r'(?<![A-Za-z0-9_])' + re.escape(term) + r'(?![A-Za-z0-9_])', re.I)


def extend_passage(row, chunks):
    """Join one neighboring verified index chunk internally, rather than ask for another tool call."""
    if row.get('domain') not in {'source', 'target', 'test'}:
        return row
    fields = ('record_id', 'sha256', 'path', 'revision', 'source_url', 'authority')
    for following in sorted(chunks, key=lambda r: r['line_start']):
        if (all(row.get(k) == following.get(k) for k in fields)
                and row['line_start'] < following['line_start'] <= row['line_end'] + 1
                and following['line_end'] > row['line_end']):
            left = row['text'].split('\n')[:row['line_end'] - row['line_start'] + 1]
            right = following['text'].split('\n')[:following['line_end'] - following['line_start'] + 1]
            overlap = row['line_end'] - following['line_start'] + 1
            if overlap and left[-overlap:] != right[:overlap]:
                raise WorkflowError('overlapping indexed evidence disagrees')
            return {**row, 'text': '\n'.join(left + right[overlap:]),
                    'line_end': following['line_end'],
                    'joined_chunk_ids': [row['chunk_id'], following['chunk_id']]}
    return row


def rank(rows, needles):
    patterns = [(term, pattern(term)) for term in needles]
    occurrences = [(row, [(term, match.start()) for term, pat in patterns
                          if (match := pat.search(row['text']))]) for row in rows]
    frequency = {term: sum(any(t == term for t, _ in hits) for _, hits in occurrences)
                 for term in needles}
    scored = []
    for row, hits in occurrences:
        if hits:
            score = sum(1 + math.log((len(rows) + 1) / (frequency[t] + 1)) for t, _ in hits)
            definitions = []
            if row.get('domain') in {'source', 'target'}:
                for term, _ in hits:
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', term):
                        continue
                    escaped = re.escape(term)
                    definition = re.search(
                        r'\b(?:struct|enum|trait|type)\s+' + escaped + r'\b|\b' + escaped +
                        r'\s*\([^;{}]{0,1200}\)\s*(?:->[^;{}]{0,300})?\{', row['text'])
                    if definition:
                        definitions.append((term, definition.start()))
                if definitions:
                    score += 8
                    hits = definitions  # Center on the body, not an earlier declaration in the chunk.
                    row = {**row, 'lookup_match': 'definition_heuristic'}
            scored.append((score, row, hits))
    return sorted(scored, key=lambda r: (-r[0], str(r[1].get('path')), r[1]['line_start']))


def window(row, hits):
    text = row['text']
    anchor = min(position for _, position in hits)
    line_start = text.rfind('\n', 0, anchor) + 1
    line_end = text.find('\n', anchor)
    line_end = len(text) if line_end < 0 else line_end + 1
    if row.get('domain') == 'obligations' and text[line_start:line_end].lstrip().startswith('|'):
        return line_start, line_end  # Do not expand a table obligation into neighboring contracts.
    before = text[:anchor].splitlines(keepends=True)
    start = sum(map(len, before[:-4])) if len(before) > 4 else 0
    # A giant line must not push the actual matched symbol outside the excerpt.
    start = max(start, anchor - 250)
    tail = text[anchor:].splitlines(keepends=True)[:65]
    end = min(len(text), anchor + sum(map(len, tail)), start + 4500)
    return start, end


def excerpt(row, hits, start, end):
    text = row['text']
    prefix = text[:start]
    stop = text[:end]
    metadata = {key: value for key, value in row.items() if key != 'text'}
    return {**metadata, 'matched_terms': [t for t, _ in hits],
            'excerpt': {'line_start': row['line_start'] + prefix.count('\n'),
                        'column_start': len(prefix.rsplit('\n', 1)[-1]),
                        'text': text[start:end]},
            'omitted_before': start > 0,
            'next': ({'line': row['line_start'] + stop.count('\n'),
                      'column': len(stop.rsplit('\n', 1)[-1]), 'sha256': row['sha256']}
                     if end < len(text) else None),
            'scope': 'Selected indexed lines only; surrounding file may contain further obligations.'}


def accepted_rows(project):
    if project.stage(S.CONTRACTS).status is not StageStatus.PASS:
        return []
    grouped = {}
    for kind in (A.CONTRACTS, A.TEST_PORT_MATRIX):
        ref = project.artifact(S.CONTRACTS, kind)
        if ref.digest in grouped:
            grouped[ref.digest]['artifact_kinds'].append(kind.value)
            continue
        if ref.size > 8 * 1024 * 1024:
            raise WorkflowError('accepted report exceeds packet reader limit; use targeted original reads')
        data = project.artifacts.read(ref)  # Bind to accepted CAS, not a mutable report copy.
        grouped[ref.digest] = {'text': data.decode('utf-8'), 'sha256': ref.digest,
                               'path': str(project.artifacts.path_for_digest(ref.digest)),
                               'artifact_kinds': [kind.value], 'domain': 'obligations',
                               'authority': 'accepted_report', 'revision': ref.digest}
    rows = []
    for document in grouped.values():
        lines = document['text'].splitlines(keepends=True)
        for start in range(0, len(lines), 60):
            body = ''.join(lines[start:start + 80])
            rows.append({**document, 'text': body, 'line_start': start + 1,
                         'line_end': min(start + 80, len(lines)),
                         'chunk_id': f"accepted-{document['sha256']}-L{start + 1}"})
    return rows


def assemble(chunks, obligations, question, *, queries=None, contract_id=None, budget=12000):
    if not question.strip() or len(question) > 600:
        raise WorkflowError('question must contain 1..600 characters')
    if type(budget) is not int or not 4096 <= budget <= 24000:
        raise WorkflowError('packet budget must be 4096..24000 UTF-8 bytes')
    queries = queries or {}
    if set(queries) - set(GROUPS) or any(not isinstance(q, str) or not q.strip() or len(q) > 240
                                      for q in queries.values()):
        raise WorkflowError('queries must be nonempty strings up to 240 characters for known groups')
    if contract_id is not None and (not contract_id.strip() or len(contract_id) > 120):
        raise WorkflowError('contract ID must contain 1..120 characters')
    from .search_results import distinct_matches
    chunks = [row for _, row in distinct_matches([(0, row) for row in chunks])]
    initial = terms(question)
    obligation_terms = [contract_id] if contract_id else terms(queries.get('obligations', question))
    found = rank(obligations, obligation_terms)
    anchors = []
    citations = []
    for _, row, hits in found[:2]:
        start, end = window(row, hits)
        # Exactly one hop from author-written code spans; no inferred aliases or graph walk.
        context = row['text'][start:end]
        for literal in re.findall(r'`([^`\n]{2,100})`', context):
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_:]*)(?:<[^<>]+>)?(?:\(\))?', literal)
            if match:
                symbol = match[1]
                if symbol.lower() not in STOP and symbol not in anchors and not re.fullmatch(r'[a-fA-F0-9]{40,}', symbol):
                    anchors.append(symbol)
        for match in re.finditer(r'([\w./-]+\.(?:c|h|rs|sh|md)):(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)', context):
            for interval in match[2].split(',')[:3]:
                bounds = [int(n) for n in interval.split('-')]
                citation = {'path': match[1], 'start': bounds[0], 'end': bounds[-1]}
                if citation not in citations and len(citations) < 6:
                    citations.append(citation)
    anchors = anchors[:8]
    needles = {group: terms(queries[group]) if group in queries else
               list(dict.fromkeys(initial + anchors))[:24] for group in GROUPS[1:]}
    ranked = {'obligations': found}
    for group in GROUPS[1:]:
        rows = [row for row in chunks if row.get('domain') == group]
        ranked[group] = rank(rows, needles[group])
        if group not in queries:
            cited = []
            for citation in citations:
                paths = {r['path'] for r in rows if r['path'] == citation['path'] or
                         r['path'].endswith('/' + citation['path'])}
                if len(paths) != 1:
                    continue  # Never guess which same-basename file a citation meant.
                for row in rows:
                    if (row['path'] in paths and row['line_start'] <= citation['end']
                            and citation['start'] <= row['line_end']):
                        offset = max(0, citation['start'] - row['line_start'])
                        position = sum(map(len, row['text'].splitlines(keepends=True)[:offset]))
                        cited.append((100, row, [(f"citation:{citation['path']}:{citation['start']}", position)]))
            ranked[group] = cited + ranked[group]
    result = {'schema_version': 1, 'question': question, 'budget_bytes': budget,
              'selection': 'literal retrieval, not inferred API equivalence or semantic coverage',
              'contract_id': contract_id, 'contract_literal_anchors': anchors,
              'contract_citations': citations,
              'groups': {g: {'query_terms': obligation_terms if g == 'obligations' else needles[g],
                             'matching_chunks': len(ranked[g]), 'items': []} for g in GROUPS},
              'notes': ['Missing hits do not prove absence. Explicit queries override automatic anchors.',
                        'Frozen target evidence is not the current modified target worktree.',
                        'Read originals beyond excerpts as needed; this packet does not certify completeness.']}
    if len(encode(result).encode()) > budget:
        raise WorkflowError('query metadata exceeds budget; shorten question or explicit queries')
    seen = {g: [] for g in GROUPS}
    active_groups = max(1, sum(bool(rows) for rows in ranked.values()))
    allowance = min(4500, max(850, (budget - len(encode(result).encode()) - 600) // active_groups))
    # Round-robin prevents a large source report from starving target/test evidence.
    queues = {g: iter(ranked[g]) for g in GROUPS}
    defined = {g: set() for g in GROUPS}
    for _ in range(2):
        for group in GROUPS:
            for _, row, hits in queues[group]:
                if row.get('lookup_match') != 'definition_heuristic' and all(term in defined[group] for term, _ in hits):
                    continue  # Do not spend a second excerpt on a prototype already defined above.
                row = extend_passage(row, chunks)
                identity = tuple(str(row.get(k, '')) for k in
                                 ('sha256', 'path', 'revision', 'authority', 'source_url', 'record_id'))
                start, end = window(row, hits)
                first = row['line_start'] + row['text'][:start].count('\n')
                last = row['line_start'] + row['text'][:end].count('\n')
                if any(key == identity and first <= hi and lo <= last for key, lo, hi in seen[group]):
                    continue
                item = excerpt(row, hits, start, end)
                # Limit each contribution so all four groups have a chance to fit.
                while len(encode(item).encode()) > allowance and end - start > 100:
                    end = start + (end - start) * 3 // 4
                    item = excerpt(row, hits, start, end)
                # Do not offer a truncated fragment that has lost every actual match.
                if not any(start <= position < end for _, position in hits):
                    continue
                items = result['groups'][group]['items']
                items.append(item)
                if len(encode(result).encode()) > budget - 600:
                    items.pop()
                    break
                seen[group].append((identity, first, last))
                if row.get('lookup_match') == 'definition_heuristic':
                    defined[group].update(term for term, _ in hits)
                break
    for group, value in result['groups'].items():
        value['status'] = ('SELECTED' if value['items'] else
                           'BUDGET_OMITTED' if value['matching_chunks'] else 'NO_LITERAL_MATCH')
        value['more_matches_possible'] = value['matching_chunks'] > len(value['items'])
    result['packet_sha256'] = hashlib.sha256(encode(result).encode()).hexdigest()
    if len(encode(result).encode()) > budget:
        raise WorkflowError('packet metadata exceeds budget; use a larger budget')
    return result


def query(project, question, *, queries=None, contract_id=None, budget=12000):
    from .index import KnowledgeIndex
    index = KnowledgeIndex.for_project(project)
    # One verified snapshot for all groups; no persisted validation cache or report.
    chunks = index._load_chunks()
    return assemble(chunks, accepted_rows(project), question, queries=queries,
                    contract_id=contract_id, budget=budget)


def command(arguments):
    from pathlib import Path
    from ..composition import open_project
    project = open_project(Path(arguments.path), read_only=True, verify_artifacts=False)
    queries = {group: value for group in GROUPS
               if (value := getattr(arguments, f'{group}_query')) is not None}
    print(encode(query(project, arguments.question, queries=queries,
                       contract_id=arguments.contract_id, budget=arguments.budget)))


def register(commands):
    parser = commands.add_parser('packet', help='bounded problem-specific code and evidence excerpts')
    parser.add_argument('path')
    parser.add_argument('--question', required=True)
    parser.add_argument('--contract-id')
    parser.add_argument('--budget', type=int, default=12000)
    for group in GROUPS:
        parser.add_argument(f'--{group}-query')
    parser.set_defaults(handler=command)
