from pathlib import Path

from driver_port_factory.codex.source_scale import advice, measure


def source(root: Path, name: str, lines: int):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("int value;\n" * lines)


def test_small_standalone_entry_ignores_large_neighbors(tmp_path):
    source(tmp_path, "evbug.c", 200)
    source(tmp_path, "unrelated.c", 20_000)
    (tmp_path / "Makefile").write_text("obj-$(CONFIG_EVBUG) += evbug.o\n")
    size = measure(tmp_path, "evbug.c")
    assert size["c_physical_lines"] == 200
    assert advice(size) is None


def test_composite_build_unit_counts_companions_without_other_drivers(tmp_path):
    source(tmp_path, "main.c", 6000)
    source(tmp_path, "hw.c", 4000)
    source(tmp_path, "optional.c", 100)
    source(tmp_path, "unrelated.c", 20_000)
    (tmp_path / "Makefile").write_text(
        "obj-$(CONFIG_NIC) += nic.o\n"
        "nic-y := main.o \\\n hw.o\n"
        "nic-$(CONFIG_OPTIONAL) += optional.o\n"
        "other-y := unrelated.o\n"
    )
    size = measure(tmp_path, "main.c")
    assert size["c_physical_lines"] == 10_100
    assert set(size["c_files"]) == {"main.c", "hw.c", "optional.c"}
    assert advice(size)["threshold_c_lines"] == 10_000


def test_threshold_and_missing_evidence_do_not_create_a_gate(tmp_path):
    source(tmp_path, "standalone.c", 9999)
    assert advice(measure(tmp_path, "standalone.c")) is None
    source(tmp_path, "standalone.c", 10_000)
    assert advice(measure(tmp_path, "standalone.c")) is not None
    assert advice(measure(tmp_path, "absent.c")) is None
    assert measure(tmp_path, "../escape.c") is None
