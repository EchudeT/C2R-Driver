"""Offline BM25 with exact identifiers and their snake/camel-case components."""

import math
import re
from collections import Counter

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_:.+-]*|[0-9]+|[\u3400-\u9fff]+")
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|[0-9]+")


def terms(text):
    result = []
    for match in TOKEN.finditer(text):
        token = match.group()
        result.append(token.casefold())
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            result.extend(token[i : i + 2] for i in range(len(token) - 1))
            continue
        parts = [p.casefold() for p in CAMEL.findall(token)]
        if parts != [token.casefold()]:
            result.extend(parts)
    return result


def bm25(chunks, query):
    wanted = set(terms(query))
    if not wanted or not chunks:
        return []
    counts = [Counter(terms(c["text"])) for c in chunks]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / len(lengths) or 1
    frequencies = Counter(t for c in counts for t in wanted if c[t])
    ranked = []
    for chunk, counts_, length in zip(chunks, counts, lengths, strict=True):
        score = 0.0
        for term in wanted:
            tf = counts_[term]
            idf = math.log(1 + (len(chunks) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
        if query.casefold() in chunk["text"].casefold():
            score += 2.0  # Preserve exact API/phrase lookup amid natural-language matches.
        if score > 0:
            ranked.append((score, chunk))
    return sorted(ranked, key=lambda pair: (-pair[0], pair[1]["chunk_id"]))


def fuse(lexical, dense, *, depth=100):
    """Reciprocal rank fusion; cosine values are not relevance probabilities."""
    scores, chunks, channels = {}, {}, {}
    for channel, ranking in (("bm25", lexical), ("dense", dense)):
        for rank, (_, chunk) in enumerate(ranking[:depth], 1):
            key = chunk["chunk_id"]
            chunks[key] = chunk
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            channels.setdefault(key, []).append(channel)
    ranked = sorted(
        ((score, chunks[key]) for key, score in scores.items()),
        key=lambda pair: (-pair[0], pair[1]["chunk_id"]),
    )
    return ranked, channels
