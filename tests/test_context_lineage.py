from driver_port_factory.control.context_report import session_lineage


def test_filtered_lineage_retains_prior_stages_and_separates_reviewer():
    jobs = [
        {"job_id": "1", "stage": "study", "thread_id": "worker", "resumed": False},
        {"job_id": "2", "stage": "review", "thread_id": "reviewer", "resumed": False},
        {"job_id": "3", "stage": "implementation", "thread_id": "worker", "resumed": True},
        {"job_id": "4", "stage": "implementation", "thread_id": "new", "resumed": False},
    ]
    report = session_lineage(jobs, stage="implementation")
    first, second = report["calls"]
    assert first["continuity"] == "resume_with_prior_record"
    assert first["prior_recorded_stages_same_thread"] == ["study"]
    assert second["continuity"] == "fresh"
    assert second["prior_recorded_calls_same_thread"] == 0
    assert report["observed_compactions"] is None


def test_missing_history_is_unknown_not_fresh_or_compacted():
    report = session_lineage([
        {"job_id": "1", "stage": "a", "thread_id": "external", "resumed": True},
        {"job_id": "2", "stage": "b", "thread_id": None},
        {"job_id": "3", "stage": "c", "thread_id": None},
    ])
    assert [c["continuity"] for c in report["calls"]] == [
        "resume_without_prior_record", "unknown", "unknown"]
    assert all(c["prior_recorded_calls_same_thread"] == 0 for c in report["calls"])
    assert report["effective_context"] == "unknown"
