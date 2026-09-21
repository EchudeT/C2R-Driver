"""Bootstrap fixture for the merged source/design task; no compiler-index gate."""

from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.migration.handoff import MigrationHandoff
from tests.knowledge_support import prepare_project
from tests.target_support import accept_target_study


def ready_project(root):
    project, checkouts = prepare_project(root)
    KnowledgeBootstrapper().build_infrastructure(project)
    accept_target_study(project)
    MigrationHandoff().create(project)
    return project, checkouts
