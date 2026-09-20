"""Disposable, digest-bound SQLite navigation; frozen evidence remains authoritative."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import ijson

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .contracts import SourceAnalysisArtifact

VERSION = 2
REBUILD_MESSAGE = (
    "C navigation index missing, stale or damaged; run knowledge c-facts-build PROJECT "
    "outside the worker sandbox (port run prepares it automatically)"
)
NOT_READY_MESSAGE = (
    "C_FACTS_NOT_READY: prepare compile_commands.json and end the source report with "
    "DPF_RUN: SOURCE_ANALYSIS. The controller will return facts in this same task."
)


def require_analysis_ready(project):
    facts_ref(project)


def facts_ref(project):
    from .preparation import artifact
    return artifact(project, SourceAnalysisArtifact.STRUCTURED_C_FACTS)


def cache_root(project):
    return project.control / "c-navigation"


def open_navigation(project):
    # Readers never create files. Lock only until the verified SQLite inode is open;
    # a later atomic rebuild cannot change the database seen by this connection.
    try:
        with (cache_root(project) / "build.lock").open("r") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return _open_navigation(project)
    except OSError as error:
        raise WorkflowError(REBUILD_MESSAGE) from error


def _open_navigation(project):
    """Validate the derived database and ALL its frozen inputs before returning a reader."""
    ref = facts_ref(project)
    root = cache_root(project)
    connection = None
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest["version"] != VERSION or manifest["facts"] != ref.digest:
            raise ValueError("stale navigation index")
        path = root / "symbols.sqlite"
        if file_sha256(path) != manifest["database"]:
            raise ValueError("damaged navigation index")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("SELECT version, facts FROM metadata").fetchone() != (
            VERSION,
            ref.digest,
        ):
            raise ValueError("navigation binding mismatch")
        facts_path = project.artifacts.path_for_digest(ref.digest)
        if (
            facts_path.stat().st_size != ref.size
            or facts_path.relative_to(project.artifacts.root).as_posix() != ref.cas_path
        ):
            raise WorkflowError("C facts metadata failed integrity verification")
        inputs = [(facts_path, ref.digest)]
        inputs.extend(
            (project.artifacts.path_for_digest(digest), digest)
            for (digest,) in connection.execute("SELECT digest FROM units")
        )
        for source, digest in dict.fromkeys(inputs):
            if file_sha256(source) != digest:
                raise WorkflowError("C navigation source failed integrity verification")
        return connection
    except WorkflowError:
        if connection is not None:
            connection.close()
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        raise WorkflowError(REBUILD_MESSAGE) from error


def prepare_navigation(project):
    """Controller-only rebuild; no changes to evidence, stage state, or worker permissions."""
    root = cache_root(project)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with closing(_open_navigation(project)):
                return
        except WorkflowError:
            pass  # Rebuild only from freshly verified evidence, never from the old cache.
        ref = facts_ref(project)
        facts = json.loads(project.artifacts.read(ref))
        with tempfile.TemporaryDirectory(prefix="build-", dir=root) as directory:
            database = Path(directory) / "symbols.sqlite"
            with closing(sqlite3.connect(database)) as db:
                _build(project, db, facts, ref.digest)
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {"version": VERSION, "facts": ref.digest, "database": file_sha256(database)}
                )
            )
            os.replace(database, root / "symbols.sqlite")
            os.replace(manifest, root / "manifest.json")


def _build(project, db, facts, digest):
    db.executescript("""
        CREATE TABLE metadata(version INTEGER, facts TEXT);
        CREATE TABLE units(unit INTEGER PRIMARY KEY, unit_id TEXT, digest TEXT);
        CREATE TABLE definitions(unit INTEGER, category TEXT, name TEXT, payload TEXT);
        CREATE INDEX symbol_lookup ON definitions(name, unit);
        CREATE TABLE calls(unit INTEGER, node TEXT, payload TEXT);
        CREATE INDEX call_lookup ON calls(unit, node);
        CREATE TABLE effects(unit INTEGER, node TEXT, kind TEXT);
        CREATE INDEX effect_lookup ON effects(unit, node);
        CREATE TABLE layouts(unit INTEGER, node TEXT, payload TEXT);
        CREATE INDEX layout_lookup ON layouts(unit, node);
        CREATE TABLE gaps(unit INTEGER, node TEXT, payload TEXT);
        CREATE INDEX gap_lookup ON gaps(unit, node);
        CREATE TABLE cfg(unit INTEGER, node TEXT, payload TEXT);
        CREATE INDEX cfg_lookup ON cfg(unit, node);
    """)
    db.execute("INSERT INTO metadata VALUES (?, ?)", (VERSION, digest))
    for number, unit in enumerate(facts["units"]):
        semantic_digest = unit["semantic_index"]["sha256"]
        path = project.artifacts.path_for_digest(semantic_digest)
        if file_sha256(path) != semantic_digest:
            raise WorkflowError("C semantic index failed integrity verification")
        with path.open("rb") as stream:
            indexes = next(ijson.items(stream, "indexes"))
        db.execute("INSERT INTO units VALUES (?, ?, ?)", (number, unit["unit_id"], semantic_digest))
        for category, identities in indexes["definition_identities"].items():
            db.executemany(
                "INSERT INTO definitions VALUES (?, ?, ?, ?)",
                ((number, category, item.get("name"), json.dumps(item)) for item in identities),
            )
        names = {
            item["node_id"]: item["name"] for item in indexes["definition_identities"]["functions"]
        }
        names.update({item["id"]: item.get("name") for item in indexes["external_declarations"]})
        pending = {
            call["target_id"]
            for call in indexes["calls"]
            if call.get("target_id") and not names.get(call["target_id"])
        }
        if pending:
            with path.open("rb") as stream:
                for node in ijson.items(stream, "nodes.item"):
                    if node["id"] in pending:
                        names[node["id"]] = node.get("name")
                        pending.remove(node["id"])
                        if not pending:
                            break
        db.executemany(
            "INSERT INTO calls VALUES (?, ?, ?)",
            (
                (
                    number,
                    call["node_id"],
                    json.dumps({**call, "target_name": names.get(call.get("target_id"))}),
                )
                for call in indexes["calls"]
            ),
        )
        db.executemany(
            "INSERT INTO effects VALUES (?, ?, ?)",
            ((number, effect["node_id"], effect["kind"]) for effect in indexes["effects"]),
        )
        for table, items in (
            ("layouts", unit["raw_facts"]["record_layout"]["summary"]["records"]),
            ("gaps", unit["raw_facts"]["cfg"]["summary"].get("unavailable_functions", [])),
            ("cfg", unit["raw_facts"]["cfg"]["summary"].get("functions", [])),
        ):
            db.executemany(
                f"INSERT INTO {table} VALUES (?, ?, ?)",
                ((number, item["ast_node_id"], json.dumps(item)) for item in items),
            )
    # Cross-TU callbacks have stable IDs but no direct target_id. Resolve names
    # after every unit is present so workers need not inspect another giant JSON.
    identities = {
        item["node_id"]: item
        for (payload,) in db.execute("SELECT payload FROM definitions WHERE category = 'functions'")
        for item in (json.loads(payload),)
    }
    for rowid, payload in db.execute("SELECT rowid, payload FROM calls").fetchall():
        call = json.loads(payload)
        if "candidate_target_ids" not in call:
            continue
        call["candidate_targets"] = [
            {"node_id": target, "name": identities.get(target, {}).get("name"),
             "source_location": identities.get(target, {}).get("source_location")}
            for target in call["candidate_target_ids"]
        ]
        db.execute("UPDATE calls SET payload = ? WHERE rowid = ?", (json.dumps(call), rowid))
    db.commit()


def details(db, project, number, category, identity, *, detail="summary"):
    unit_id, digest = db.execute(
        "SELECT unit_id, digest FROM units WHERE unit = ?", (number,)
    ).fetchone()
    result = {
        "unit_id": unit_id,
        "category": category,
        "identity": identity,
        "semantic_index": str(project.artifacts.path_for_digest(digest)),
    }
    node = identity["node_id"]
    if category == "records":
        result["layouts"] = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM layouts WHERE unit = ? AND node = ? ORDER BY rowid",
                (number, node),
            )
        ]
        return result
    # Hierarchical AST IDs: lexical range preserves startswith(node + '.').
    parameters = (number, node + ".", node + "/")
    where = "unit = ? AND node >= ? AND node < ?"
    count = db.execute(f"SELECT count(*) FROM calls WHERE {where}", parameters).fetchone()[0]
    result.update(
        {
            "call_count": count,
            "calls": [
                json.loads(row[0])
                for row in db.execute(
                    f"SELECT payload FROM calls WHERE {where} ORDER BY rowid LIMIT 20", parameters
                )
            ],
            "calls_truncated": count > 20,
            "effect_counts": dict(
                db.execute(
                    f"SELECT kind, count(*) FROM effects WHERE {where} "
                    "GROUP BY kind ORDER BY min(rowid)",
                    parameters,
                )
            ),
            "cfg_gaps": [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM gaps WHERE unit = ? AND node = ? ORDER BY rowid",
                    (number, node),
                )
            ],
        }
    )
    if detail == "calls":
        result["calls"] = [json.loads(row[0]) for row in db.execute(
            f"SELECT payload FROM calls WHERE {where} ORDER BY rowid", parameters
        )]
        result["calls_truncated"] = False
    elif detail == "cfg":
        result["cfg"] = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM cfg WHERE unit = ? AND node = ? ORDER BY rowid",
            (number, node),
        )]
    return result
