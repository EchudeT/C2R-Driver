import json
import pytest

from driver_port_factory.read_evidence import read_text


def test_long_unicode_lines_roundtrip_and_detect_changes(tmp_path):
    path = tmp_path / "report.md"
    original = '# State\n' + '中文"\\\t' * 1500 + '\nlast\n'
    path.write_text(original)
    cursor, parts = {}, []
    while True:
        page = read_text(path, budget=1000, **cursor)
        assert len(json.dumps(page, ensure_ascii=False)) < 1600
        parts.extend(item["text"] for item in page["excerpts"])
        if not page["next"]:
            break
        cursor = page["next"]
    assert ''.join(parts) == original
    path.write_text('changed')
    assert read_text(path, **cursor)["status"] == "CONTENT_CHANGED"


def test_heading_and_literal_queries_preserve_locations(tmp_path):
    path = tmp_path / 'report.md'
    path.write_text('# Current\nPASS but device not tested\n## History\nfailed\n')
    assert [r['line'] for r in read_text(path, headings=True)['excerpts']] == [1, 3]
    page = read_text(path, contains='device')
    assert [r['line'] for r in page['excerpts']] == [2]
    assert page['semantic_verdict'] == 'NOT_EVALUATED'


def test_budget_utilization_and_filtered_cursor(tmp_path):
    path = tmp_path / 'large.txt'
    path.write_text('match ' + 'a' * 10000 + '\nskip this\nmatch end\n')
    page = read_text(path, budget=1000, contains='match')
    assert len(page['excerpts'][0]['text']) > 900
    parts = [item['text'] for item in page['excerpts']]
    while page['next']:
        page = read_text(path, budget=1000, **page['next'])
        parts.extend(item['text'] for item in page['excerpts'])
    assert ''.join(parts) == 'match ' + 'a' * 10000 + '\nmatch end\n'
    with pytest.raises(ValueError, match='start outside'):
        read_text(path, start=999)
