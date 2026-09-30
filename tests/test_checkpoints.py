import fcntl
import json
import sqlite3
from types import SimpleNamespace

import pytest

from driver_port_factory.checkpoints import (
    create, fork, git, load, verify, digest, storage, capture_invocation,
)
from driver_port_factory.core.models import WorkflowError


def fixture(tmp_path):
    root = tmp_path / 'run'
    control = root / '.dpf'
    control.mkdir(parents=True)
    config = control / 'project.json'
    config.write_text('{}')
    db = control / 'run.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE example (value TEXT)')
        conn.execute("INSERT INTO example VALUES ('frozen')")
    source = root / 'work/source'
    source.mkdir(parents=True)
    git(source, 'init', '--quiet')
    (source / '.gitignore').write_text('build/\n')
    (source / 'driver.c').write_text('original\n')
    (source / 'removed.c').write_text('delete later\n')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'base')
    obj = control / 'cas/object'
    obj.parent.mkdir()
    obj.write_text('immutable contract')
    artifact = SimpleNamespace(digest=digest(obj), size=obj.stat().st_size)
    project = SimpleNamespace(root=root, control=control, database_path=db, config_path=config,
                              artifact_refs=lambda: [artifact],
                              artifacts=SimpleNamespace(path_for_digest=lambda _: obj))
    return project, source, obj


def test_git_checkpoint_freezes_dirty_tree_without_touching_index_and_forks_isolated_arms(tmp_path):
    project, source, _ = fixture(tmp_path)
    (source / 'driver.c').write_text('staged\n')
    git(source, 'add', 'driver.c')
    (source / 'driver.c').write_text('unstaged\n')
    (source / 'removed.c').unlink()
    (source / 'new.rs').write_text('new\n')
    (source / 'build').mkdir()
    (source / 'build/huge').write_bytes(b'x' * 3_000_000)
    (source / 'alias').symlink_to('driver.c')
    head = git(source, 'rev-parse', 'HEAD')
    index = git(source, 'ls-files', '--stage')
    report = project.root / 'analysis.md'
    report.write_text('accepted analysis')
    create(project, 'before-delivery', sources=[source], materials=[report])
    assert git(source, 'rev-parse', 'HEAD') == head
    assert git(source, 'ls-files', '--stage') == index
    (source / 'driver.c').write_text('later change\n')
    report.write_text('later analysis')
    for arm in ('a', 'b'):
        fork(project, 'before-delivery', tmp_path / arm)
        checkout = tmp_path / arm / 'source-0'
        assert (checkout / 'driver.c').read_text() == 'unstaged\n'
        assert not (checkout / 'removed.c').exists()
        assert (checkout / 'new.rs').read_text() == 'new\n'
        assert (checkout / 'alias').is_symlink()
        assert not (checkout / 'build').exists()
        assert (tmp_path / arm / 'inputs/material-0-analysis.md').read_text() == 'accepted analysis'
        assert not (tmp_path / arm / '.dpf').exists()  # no accidental controller clone/PASS
    (tmp_path / 'a/source-0/driver.c').write_text('experiment a\n')
    assert (tmp_path / 'b/source-0/driver.c').read_text() == 'unstaged\n'
    assert (source / 'driver.c').read_text() == 'later change\n'
    assert load(project, 'before-delivery')['native_session_fork'] is False


def test_shared_cas_corruption_is_detected_before_fork(tmp_path):
    project, source, obj = fixture(tmp_path)
    create(project, 'one', sources=[source])
    assert verify(project, 'one')['label'] == 'one'
    obj.write_text('changed')
    with pytest.raises(WorkflowError, match='shared object'):
        fork(project, 'one', tmp_path / 'arm')
    assert not (tmp_path / 'arm').exists()


def test_checkpoint_excludes_large_logs_and_builds_reuses_git_blobs(tmp_path):
    project, source, obj = fixture(tmp_path)
    create(project, 'one', sources=[source])
    create(project, 'two', sources=[source])
    a, b = load(project, 'one'), load(project, 'two')
    assert a['sources'][0]['tree'] == b['sources'][0]['tree']
    assert a['artifacts'] == b['artifacts']
    assert a['artifacts'][0]['path'] == str(obj)
    names = git(storage(project), 'ls-tree', '--name-only', 'refs/checkpoints/one').decode()
    assert 'run.sqlite3' in names and 'object' not in names
    first = git(storage(project), 'rev-parse', 'refs/checkpoints/one:run.sqlite3')
    second = git(storage(project), 'rev-parse', 'refs/checkpoints/two:run.sqlite3')
    assert first == second
    with pytest.raises(WorkflowError, match='already exists'):
        create(project, 'one', sources=[source])


def test_checkpoint_requires_idle_controller_and_rejects_large_new_source(tmp_path):
    project, source, _ = fixture(tmp_path)
    with (project.control / 'controller.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        with pytest.raises(WorkflowError, match='idle'):
            create(project, 'locked', sources=[source])
    (source / 'binary').write_bytes(b'x' * 3_000_000)
    git(source, 'add', 'binary')  # staged additions must also be bounded
    with pytest.raises(WorkflowError, match='large changed'):
        create(project, 'large', sources=[source])


def test_native_log_chunks_are_referenced_and_snapshot_manifest_is_frozen(tmp_path):
    project, source, _ = fixture(tmp_path)
    logs = project.control / 'codex/context-logs'
    objects = logs / 'objects'
    objects.mkdir(parents=True)
    chunk = objects / 'temp'
    chunk.write_text('native historical conversation')
    sha = digest(chunk)
    chunk.rename(objects / sha)
    native = {'snapshots': [{'chunks': [{'sha256': sha, 'bytes': 30}]}]}
    native['snapshots'][0]['chunks'][0]['bytes'] = (objects / sha).stat().st_size
    (logs / 'call.json').write_text(json.dumps(native))
    create(project, 'one', sources=[source])
    (logs / 'call.json').write_text('{}')
    assert verify(project, 'one')['log_objects'][0]['sha256'] == sha
    frozen = json.loads(git(storage(project), 'show', 'refs/checkpoints/one:context-0.json'))
    assert frozen == native
    (objects / sha).unlink()
    with pytest.raises(WorkflowError, match='shared object'):
        verify(project, 'one')


@pytest.mark.parametrize('error_type', [WorkflowError, KeyError, OSError])
def test_optional_capture_never_blocks_translation(tmp_path, monkeypatch, error_type):
    project, source, _ = fixture(tmp_path)
    metrics = {'job_id': 'a', 'model': 'example'}
    monkeypatch.delenv('DPF_EXPERIMENT_CHECKPOINTS', raising=False)
    capture_invocation(project, 'driver_implementation', metrics, 'prompt')
    assert 'experiment_checkpoint' not in metrics
    monkeypatch.setenv('DPF_EXPERIMENT_CHECKPOINTS', '1')
    def failure(*args, **kwargs):
        raise error_type('capture unavailable')
    monkeypatch.setattr('driver_port_factory.checkpoints.create', failure)
    capture_invocation(project, 'driver_implementation', metrics, 'prompt')
    assert metrics['experiment_checkpoint']['status'] == 'unavailable'


def test_frozen_reader_survives_later_edits_and_supports_bounded_continuation(tmp_path):
    from driver_port_factory.checkpoints import read_source
    project, source, _ = fixture(tmp_path)
    original = 'x' * 1500 + '\noriginal tail\n'
    (source / 'driver.c').write_text(original)
    create(project, 'one', sources=[source])
    (source / 'driver.c').write_text('later content')
    first = read_source(project, 'one', 0, 'driver.c', budget=256)
    assert first['next'] is not None
    second = read_source(project, 'one', 0, 'driver.c', start=first['next']['start'],
                         column=first['next']['column'], budget=2000)
    assert ''.join(x['text'] for x in first['excerpts'] + second['excerpts']) == original
    assert first['sha256'] == second['sha256']
    assert 'later content' not in json.dumps(first)


def test_two_worktrees_in_same_repository_have_distinct_gc_pins(tmp_path):
    project, source, _ = fixture(tmp_path)
    other = project.root / 'work/other'
    git(source, 'worktree', 'add', '--detach', str(other), 'HEAD')
    (other / 'driver.c').write_text('other tree')
    create(project, 'one', sources=[source, other])
    items = load(project, 'one')['sources']
    assert items[0]['pin'] != items[1]['pin']
    for item in items:
        assert git(source, 'rev-parse', item['pin']).decode().strip() == item['commit']
    fork(project, 'one', tmp_path / 'arm')
    assert not (tmp_path / 'arm/source-0').exists()  # only target gets a checkout by default
    assert (tmp_path / 'arm/source-1/driver.c').read_text() == 'other tree'


def test_failed_capture_releases_source_pins(tmp_path):
    project, source, _ = fixture(tmp_path)
    with pytest.raises(WorkflowError, match='material'):
        create(project, 'one', sources=[source], materials=[project.root / 'missing'])
    assert not git(source, 'for-each-ref', 'refs/dpf-checkpoints/').strip()


def test_fork_rejects_symlinks_back_into_original_workspace(tmp_path):
    project, source, _ = fixture(tmp_path)
    (source / 'unsafe-link').symlink_to(source / 'driver.c')
    create(project, 'one', sources=[source])
    with pytest.raises(WorkflowError, match='symlink escapes'):
        fork(project, 'one', tmp_path / 'arm')
    assert (tmp_path / 'arm/INCOMPLETE').exists()
    assert (source / 'driver.c').read_text() == 'original\n'


def test_auto_capture_under_controller_lock_records_fresh_task_provenance(tmp_path, monkeypatch):
    from driver_port_factory.checkpoints import lock
    import hashlib
    project, source, _ = fixture(tmp_path)
    monkeypatch.setenv('DPF_EXPERIMENT_CHECKPOINTS', '1')
    monkeypatch.setattr('driver_port_factory.checkpoints.default_sources', lambda _: [source])
    metrics = {'job_id': 'test-job', 'stage': 'driver_implementation', 'model': 'test-model',
               'call_reason': 'repair', 'thread_id': 'audit-only-thread'}
    with lock(project.control / 'controller.lock'):
        capture_invocation(project, 'driver_implementation', metrics, 'frozen prompt')
    assert metrics['experiment_checkpoint']['capture_seconds'] >= 0
    frozen = load(project, 'call-test-job')
    assert frozen['invocation']['prompt_sha256'] == hashlib.sha256(b'frozen prompt').hexdigest()
    assert frozen['invocation']['call_reason'] == 'repair'
    assert frozen['mode'] == 'fresh-task'
    assert frozen['native_session_fork'] is False
    assert len(frozen['engine_sha256']) == 64
