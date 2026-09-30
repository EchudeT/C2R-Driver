"""Bounded, deterministic source navigation, never inferred semantic authority."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .index import KnowledgeIndex, file_sha256

# These are search leads, not exhaustive API lists or a driver classifier.
TOPICS = {
    'lifecycle': r'\b\w*(?:probe|remove|attach|detach|register|unregister|reset|shutdown)\w*\b',
    'dma_layout': r'\b\w*(?:dma|descriptor|packed|align|endian|le32|be32)\w*\b',
    'io_ordering': r'\b\w*(?:mmio|ioread|iowrite|readl|writel|barrier|fence|volatile)\w*\b',
    'concurrency': r'\b\w*(?:irq|interrupt|spinlock|mutex|atomic|workqueue)\w*\b',
    'queues': r'\b\w*(?:ring|queue|head|tail|wrap)\w*\b',
    'errors_cleanup': r'\b(?:goto|return|Drop|Result|ERR_PTR|IS_ERR)\b|\b\w*(?:free|release|unmap)\w*\b',
    'declarations': r'^\s*(?:#\s*(?:define|include|if\w*)|(?:pub\s+)?(?:struct|enum|union|trait|impl)\b)',
}
PATTERNS = {key: re.compile(value, re.I) for key, value in TOPICS.items()}
PROBES = {
    'lifecycle': 'If lifecycle mapping is unresolved, compile a minimal registration/cleanup path.',
    'dma_layout': 'Check actual layout/alignment/endian and DMA ownership APIs before implementing the data path.',
    'io_ordering': 'Resolve register access width and ordering against original device/target evidence.',
    'concurrency': 'Resolve IRQ context, blocking and lock ordering; a compile pass cannot prove concurrency safety.',
    'queues': 'Consider boundary/wraparound input vectors for pure queue arithmetic.',
    'errors_cleanup': 'Consider initialization failure and partial-resource cleanup tests.',
}
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 24_000_000
MAX_HITS = 16  # per file/topic; omissions explicitly counted


def scan(text):
    """Keep original line numbers; suppress ordinary comments/strings, not claim parsing."""
    lexical = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                     lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]),
                     text, flags=re.S)
    hits, totals = [], Counter()
    originals = text.splitlines()
    for number, line in enumerate(lexical.splitlines(), 1):
        for topic, pattern in PATTERNS.items():
            if pattern.search(line):
                totals[topic] += 1
                if totals[topic] <= MAX_HITS:
                    hits.append({'topic': topic, 'line': number, 'excerpt': originals[number - 1][:240]})
    return hits, dict(totals)


def prepare(project):
    index = KnowledgeIndex.for_project(project)
    records = index.verified_records()
    identity = {'manifest': index.manifest.digest, 'extractor': file_sha256(Path(__file__))}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = project.control / 'translation-facts'
    directory.mkdir(exist_ok=True)
    path = directory / f'{key}.json'
    digest_path = path.with_suffix('.sha256')
    if path.is_file() and digest_path.is_file() and file_sha256(path) == digest_path.read_text().strip():
        value = json.loads(path.read_text())
    else:
        files, skipped, total = [], [], 0
        seen = set()
        for record in sorted(records, key=lambda r: (r['domain'], r['path'])):
            source = index.controlled_path(record['path'])
            if (record['domain'], record['path']) in seen:
                continue
            seen.add((record['domain'], record['path']))
            if source.suffix.lower() not in {'.c', '.h', '.rs', '.cc', '.cpp'}:
                continue
            size = source.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                skipped.append({'path': record['path'], 'reason': 'bounded_scan_size_limit'})
                continue
            total += size
            try:
                hits, counts = scan(source.read_text(encoding='utf-8'))
            except UnicodeDecodeError:
                skipped.append({'path': record['path'], 'reason': 'non_utf8'})
                continue
            files.append({'path': str(source), 'domain': record['domain'], 'sha256': record['sha256'],
                          'revision': record['revision'], 'source_url': record['source_url'],
                          'hits': hits, 'total_matches': counts,
                          'omitted_matches': sum(max(0, n - MAX_HITS) for n in counts.values())})
        topics = sorted({h['topic'] for f in files if f['domain'] == 'source' for h in f['hits']})
        value = {'identity': identity, 'files': files, 'skipped': skipped,
                 'probe_leads': {t: PROBES[t] for t in topics if t in PROBES},
                 'limits': 'Lexical navigation only. No macro expansion, configuration resolution, '
                 'call graph, API equivalence or completeness proof. Rust raw strings and exotic '
                 'syntax may yield false leads. No match does not mean no obligation. '
                 'Read originals and frozen contracts; do not run every suggested probe by default.'}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
        digest_path.write_text(file_sha256(path) + '\n')
    # Do not paste the full index into every model prompt.
    return {'path': str(path), 'sha256': file_sha256(path), 'file_count': len(value['files']),
            'skipped_files': len(value['skipped']), 'probe_topics': list(value['probe_leads']),
            'instruction': 'Read relevant source/target locations on demand. Lexical leads, not semantic '
            'findings. Resolve only design-changing uncertainties with small probes; no extra report required.'}


def query(project, *, topic=None, domain=None, limit=12):
    if not 1 <= limit <= 50:
        from ..core.models import WorkflowError
        raise WorkflowError('Fact lookup limit must be between 1 and 50')
    packet = prepare(project)
    value = json.loads(Path(packet['path']).read_text())
    matches = [{**{k: f[k] for k in ('path', 'domain', 'sha256', 'revision')}, **hit}
               for f in value['files'] if domain is None or f['domain'] == domain
               for hit in f['hits'] if topic is None or hit['topic'] == topic]
    return {'packet': packet, 'matches': matches[:limit], 'available_matches': len(matches),
            'returned': min(limit, len(matches)), 'limits': value['limits'],
            'instruction': 'For omitted locations, use the packet or targeted original-source search.'}
