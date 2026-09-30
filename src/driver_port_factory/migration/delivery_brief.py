"""Exact, bounded navigation from the accepted analysis; never inferred requirements."""
import re

from ..core.models import StageStatus
from .contracts import MigrationArtifact as A, MigrationStage as S


def excerpt(text, *, budget=5000, heading='Delivery essentials'):
    lines = text.splitlines(keepends=True)
    fenced = False
    start = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        if not fenced and re.fullmatch(r'##\s+' + re.escape(heading) + r'\s*', line.strip(), re.I):
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    fenced = False
    for index in range(start, len(lines)):
        line = lines[index]
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        if not fenced and re.match(r'^#{1,2}\s', line):
            end = index
            break
    body = ''.join(lines[start:end])
    if not body.strip():
        return None
    return {'text': body[:budget], 'line_start': start + 1,
            'truncated': len(body) > budget, 'section_characters': len(body)}


def context(project):
    if project.stage(S.CONTRACTS).status is not StageStatus.PASS:
        return None
    reference = project.artifact(S.CONTRACTS, A.CONTRACTS)
    packet = excerpt(project.artifacts.read(reference).decode('utf-8'))
    if packet is None:
        return None  # No compulsory template, rework request or model summarization.
    return {**packet, 'source': {'kind': reference.kind, 'digest': reference.digest,
                               'path': str(project.artifacts.path_for_digest(reference.digest))},
            'instruction': 'Exact author-written excerpt, not a controller coverage verdict. '
            'Use this to plan necessary driver/test entrypoints before coding. All frozen obligations '
            'remain authoritative, including those outside or omitted from this bounded excerpt.'}
