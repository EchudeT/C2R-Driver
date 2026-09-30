import json
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.knowledge.problem_packet import assemble, encode, query, accepted_rows
from driver_port_factory.knowledge.index import KnowledgeIndex
from tests.workflow_support import ready_implementation


def row(domain, text, **changes):
    return {'domain': domain, 'path': f'{domain}/driver.c', 'revision': 'frozen',
            'sha256': 'a' * 64, 'source_url': 'https://example.invalid/original',
            'record_id': domain, 'chunk_id': domain + '-L1', 'line_start': 1,
            'line_end': len(text.splitlines()), 'text': text, **changes}


def materials():
    obligations = [row('obligations', '## C-RX\nOn allocation failure `clean_rx` releases ownership.\n'
                       'Target `DmaBuffer` must not be published early. Test `alloc_fail` observes cleanup.\n')]
    chunks = [row('source', 'void clean_rx(void) {\n  release();\n}\n'),
              row('target', 'impl DmaBuffer {\n  fn release(self) {}\n}\n'),
              row('test', 'void alloc_fail(void) { assert(cleaned); }\n')]
    return chunks, obligations


def test_one_call_groups_contract_source_target_and_tests_without_inferred_mapping():
    chunks, obligations = materials()
    result = assemble(chunks, obligations, '分配失败时如何保持正确所有权？', contract_id='C-RX')
    assert result['contract_literal_anchors'] == ['clean_rx', 'DmaBuffer', 'alloc_fail']
    for group in ('obligations', 'source', 'target', 'test'):
        assert result['groups'][group]['status'] == 'SELECTED'
        item = result['groups'][group]['items'][0]
        assert item['sha256'] == 'a' * 64
        assert item['revision'] == 'frozen'
        assert item['excerpt']['text'] in (obligations + chunks)[['obligations', 'source', 'target', 'test'].index(group)]['text']
    assert len(encode(result).encode()) <= 12000
    assert 'not inferred API equivalence' in result['selection']


def test_explicit_group_query_overrides_expansion_and_does_not_broaden_on_miss():
    chunks, obligations = materials()
    result = assemble(chunks, obligations, 'C-RX', queries={'target': 'unrelated_exact_api'})
    assert result['groups']['source']['status'] == 'SELECTED'
    assert result['groups']['target']['status'] == 'NO_LITERAL_MATCH'
    assert result['groups']['target']['query_terms'] == ['unrelated_exact_api']


@pytest.mark.parametrize('budget', [4096, 8000, 12000, 24000])
def test_total_utf8_budget_and_bounded_group_output(budget):
    chunks, obligations = materials()
    chunks = [row(domain, 'clean_rx DmaBuffer alloc_fail 中文字节\n' * 500,
                  path=f'{domain}/{i}.c', chunk_id=f'{domain}-{i}')
              for domain in ('source', 'target', 'test') for i in range(12)]
    result = assemble(chunks, obligations, 'C-RX', budget=budget)
    assert len(encode(result).encode()) <= budget
    assert all(len(group['items']) <= 2 for group in result['groups'].values())
    if budget >= 8000:
        assert all(group['items'] for group in result['groups'].values())
    assert any(group['more_matches_possible'] for group in result['groups'].values())


def test_empty_search_does_not_fill_packet_with_irrelevant_background():
    chunks, obligations = materials()
    result = assemble(chunks, obligations, '一个没有匹配标识符的问题')
    assert all(not group['items'] for group in result['groups'].values())
    assert 'Missing hits do not prove absence' in result['notes'][0]


def test_long_line_excerpt_retains_match_and_exact_column():
    text = 'z' * 3000 + ' clean_rx ' + 'y' * 10000
    result = assemble([row('source', text, line_start=51)], [], 'clean_rx')
    item = result['groups']['source']['items'][0]
    part = item['excerpt']
    assert part['line_start'] == 51
    assert part['column_start'] > 0
    assert 'clean_rx' in part['text']
    assert text[part['column_start']:part['column_start'] + len(part['text'])] == part['text']
    assert item['next']['sha256'] == 'a' * 64


def test_no_recursive_expansion_from_source_to_unrelated_target():
    chunks, obligations = materials()
    chunks[0]['text'] += '\n`unrelated_symbol`\n'
    chunks.append(row('target', 'struct unrelated_symbol {}', path='target/other.rs'))
    result = assemble(chunks, obligations, 'C-RX')
    assert all('unrelated_symbol' not in item['excerpt']['text']
               for item in result['groups']['target']['items'])


def test_single_verified_index_load_and_integrity_failure_is_not_hidden(tmp_path):
    project = ready_implementation(tmp_path)
    index = object.__new__(KnowledgeIndex)
    chunks, obligations = materials()
    with patch.object(KnowledgeIndex, 'for_project', return_value=index), \
         patch.object(index, '_load_chunks', return_value=chunks) as load, \
         patch('driver_port_factory.knowledge.problem_packet.accepted_rows', return_value=obligations):
        result = query(project, 'C-RX')
        assert result['groups']['target']['items']
        load.assert_called_once()
        load.side_effect = WorkflowError('hash mismatch')
        with pytest.raises(WorkflowError, match='hash mismatch'):
            query(project, 'C-RX')


def test_shared_accepted_report_is_verified_once_and_retains_both_roles(tmp_path):
    project = ready_implementation(tmp_path)
    with patch.object(project.artifacts, 'read', wraps=project.artifacts.read) as read:
        result = accepted_rows(project)
        assert read.call_count == 1
    assert result
    assert set(result[0]['artifact_kinds']) == {'migration_contracts', 'test_port_matrix'}
    assert result[0]['authority'] == 'accepted_report'


def test_queries_and_metadata_are_serializable_and_stable():
    chunks, obligations = materials()
    result = assemble(chunks, obligations, 'C-RX')
    assert json.loads(encode(result)) == result
    assert assemble(chunks, obligations, 'C-RX') == result


def test_identical_index_aliases_do_not_duplicate_text_but_keep_provenance():
    chunks, obligations = materials()
    alias = {**chunks[0], 'chunk_id': 'alias-chunk', 'record_id': 'alias-record'}
    result = assemble(chunks + [alias], obligations, 'C-RX')
    source = result['groups']['source']['items']
    assert len(source) == 1
    assert source[0]['also_indexed_as'] == [{'record_id': 'alias-record', 'chunk_id': 'alias-chunk'}]
    different = {**alias, 'revision': 'other-revision'}
    result = assemble(chunks + [different], obligations, 'C-RX')
    assert len(result['groups']['source']['items']) == 2


def test_table_contract_follows_explicit_citation_and_generic_type_in_one_packet():
    obligations = [row('obligations', '| C-X | `driver.c:101-103` uses `DmaBuffer<Device>` |\n'
                       '| C-OTHER | `unrelated_symbol` is unrelated |\n')]
    chunks = [row('source', 'void actual_cleanup(void) { release(); }\n', path='src/driver.c',
                  line_start=101, line_end=101),
              row('target', 'struct DmaBuffer<T> {}\n'),
              row('target', 'struct unrelated_symbol {}\n', path='other.rs')]
    result = assemble(chunks, obligations, '如何实现这一条？', contract_id='C-X')
    assert result['contract_literal_anchors'] == ['DmaBuffer']
    assert result['groups']['source']['items'][0]['matched_terms'] == ['citation:driver.c:101']
    assert len(result['groups']['target']['items']) == 1
    assert 'C-OTHER' not in result['groups']['obligations']['items'][0]['excerpt']['text']


def test_ambiguous_basename_citation_is_not_guessed():
    obligations = [row('obligations', '| C-X | `driver.c:101-103` |\n')]
    chunks = [row('source', 'void cleanup(void) {}', path=f'{prefix}/driver.c',
                  line_start=101, line_end=101) for prefix in ('one', 'two')]
    result = assemble(chunks, obligations, 'C-X')
    assert not result['groups']['source']['items']


def test_definition_precedes_prototype_and_call_site():
    chunks = [row('source', 'void clean_rx(void);', line_start=1),
              row('source', 'clean_rx();', line_start=40, chunk_id='call'),
              row('source', 'void clean_rx(void) {\n  release();\n}\n', line_start=101, chunk_id='body')]
    result = assemble(chunks, [], 'clean_rx')
    assert 'release();' in result['groups']['source']['items'][0]['excerpt']['text']


def test_neighboring_chunks_join_internally_without_another_read():
    lines = ['/* filler */'] * 150
    lines[74] = 'void clean_rx(void) {'
    lines[90] = 'if (length > capacity) return;'
    lines[100] = '}'
    chunks = [row('source', '\n'.join(lines[:80]), line_start=1, line_end=80),
              row('source', '\n'.join(lines[60:140]), line_start=61, line_end=140, chunk_id='next')]
    result = assemble(chunks, [], 'clean_rx')
    item = result['groups']['source']['items'][0]
    assert 'length > capacity' in item['excerpt']['text']
    assert item['joined_chunk_ids'] == ['source-L1', 'next']
    assert len(result['groups']['source']['items']) == 1
