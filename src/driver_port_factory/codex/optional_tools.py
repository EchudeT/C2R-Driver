"""Small, stage-specific navigation; detailed instructions are loaded on demand."""
from pathlib import Path
import sys

STAGES = {'target_platform_study', 'migration_contracts', 'target_framework_enablement',
          'driver_implementation', 'artifact_preparation', 'public_qemu_validation'}
GUIDE = Path(__file__).resolve().parents[1] / 'data/prompt-packs/default/optional-tools.md'


def context(project, stage):
    if stage.value not in STAGES:
        return {}
    prefix = [sys.executable, '-m', 'driver_port_factory.cli']
    return {'problem_packet': [*prefix, 'knowledge', 'packet', str(project.root)],
            'translation_facts': [*prefix, 'knowledge', 'facts', str(project.root)],
            'semantic_lookup': [*prefix, 'knowledge', 'semantic', str(project.root)],
            'source_cases': [*prefix, 'experiment', 'generate-cases', str(project.root)],
            'early_probe': [*prefix, 'experiment', 'probe', str(project.root)],
            'optional_tools_guide': str(GUIDE),
            'optional_tools_rule': 'Use only to resolve a concrete uncertainty or obligation. '
            'Default bounded output/case budget; no automatic tool, model, test or review cascade.'}
