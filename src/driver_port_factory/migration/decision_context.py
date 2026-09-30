"""Exact, bounded accepted decisions and route handoff; no inferred blockers."""
import json

from ..core.models import StageStatus
from ..environment.contracts import EnvironmentStage as ES, EnvironmentArtifact as E
from ..target_study.contracts import TargetStudyStage as TS, TargetStudyArtifact as T
from .contracts import MigrationStage as S, MigrationArtifact as A
from .delivery_brief import excerpt


def context(project):
    result = {}
    if project.stage(ES.RECOVERY).status is StageStatus.PASS:
        ref = project.artifact(ES.RECOVERY, E.EXPERIMENT_ROUTE)
        route = json.loads(project.artifacts.read(ref))
        selected = {key: route[key] for key in ('artifact_mode', 'command', 'cwd', 'environment',
                                               'driver_insertion_or_packaging_path') if key in route}
        source = {'kind': ref.kind, 'digest': ref.digest,
                  'path': str(project.artifacts.path_for_digest(ref.digest))}
        result['accepted_route'] = {'source': source,
            'instruction': 'Accepted environment route, not proof that current driver code is delivered. '
                           'Reuse the recorded invocation/configuration where applicable; check current '
                           'tool/image identity before execution. Do not rediscover an unrelated build route.'}
        if len(json.dumps(selected, ensure_ascii=False).encode()) <= 2400:
            result['accepted_route']['recipe'] = selected
        else:
            result['accepted_route']['recipe_omitted'] = 'Read the bound route; oversized recipes are not truncated into executable commands.'
    # Accepted contracts supersede platform study. Never resurface a resolved old study decision.
    if project.stage(S.CONTRACTS).status is not StageStatus.PASS and any(
            ref.kind == A.CONTRACTS.value for ref in project.artifact_refs(stage=S.CONTRACTS)):
        return result
    owner, kind = ((S.CONTRACTS, A.CONTRACTS) if project.stage(S.CONTRACTS).status is StageStatus.PASS
                   else (TS.STUDY, T.REPORT))
    if project.stage(owner).status is StageStatus.PASS:
        ref = project.artifact(owner, kind)
        body = excerpt(project.artifacts.read(ref).decode(), budget=2000, heading='Open decisions')
        if body:
            result['open_decisions'] = {**body, 'source': {'kind': ref.kind, 'digest': ref.digest,
                'path': str(project.artifacts.path_for_digest(ref.digest))},
                'instruction': 'Exact author-written uncertainties, not controller findings. Resolve only '
                               'those that change the current implementation route, using existing evidence '
                               'first and at most the smallest necessary probe. Absence is not proof all '
                               'uncertainty is resolved; no extra planning or review turn is required.'}
    return result
