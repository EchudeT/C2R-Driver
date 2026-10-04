"""Explicit frozen inputs at phase boundaries, separate from model invocation."""

from ..intake.behavior_scope import RULE, effective


def prepare(project, stage, context):
    value = dict(context or {})
    if stage.value == "evidence_closure":
        from ..knowledge.acquisition_context import context as acquisition_context

        value["evidence_reuse"] = acquisition_context(project)
    if stage.value == "environment_recovery":
        from ..environment.context import context as environment_context

        value["environment_setup"] = environment_context(project)
    if stage.value in {"target_platform_study", "migration_contracts"}:
        from ..target_study.inputs import context as analysis_inputs

        value["analysis_entry"] = analysis_inputs(project)
    if stage.value == "target_platform_study":
        from ..knowledge.reuse import optional_context

        value["prior_experience"] = optional_context(
            project, stage.value, project.config.driver_name
        )
    value["functional_scope"] = {"boundary": effective(project.config), "rule": RULE}
    if stage.value in {"target_platform_study", "migration_contracts", "driver_implementation"}:
        from ..platform.public_tests import context as public_test_context

        prepared = public_test_context(project)
        if prepared:
            value["prepared_public_tests"] = prepared
    return value
