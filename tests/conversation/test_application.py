"""Casos de uso con dobles de los puertos: motor Actor-Critic-Boss y
mantenimiento de la memoria (resumen, anclas, metadata)."""

import pytest

from app.conversation.application.estimation_engine import EstimationEngine
from app.conversation.application.memory_maintainer import MemoryMaintainer
from app.conversation.domain.memory import ConversationMemory
from app.conversation.domain.metadata import ProjectMetadata
from app.conversation.domain.review import CriticReport, CriticVerdict, EstimationMode
from app.conversation.domain.usage import CallRole
from app.conversation.ports import ConversationSnapshot, ConversationSummary
from app.ports.llm import LLMUsage, ModelTier, StructuredCompletion
from app.schemas import EstimationResult
from tests.conftest import VALID_RESULT

USAGE = LLMUsage("m", "p", 1.0, 10, 5, 0.001)
ACTOR_RESULT = EstimationResult.model_validate(VALID_RESULT)
BOSS_RESULT = ACTOR_RESULT.model_copy(update={"summary": "Estimación revisada por el boss."})
SNAPSHOT = ConversationSnapshot("<transcript>x</transcript>", ProjectMetadata(), "", ())


def done(parsed) -> StructuredCompletion:
    return StructuredCompletion(parsed=parsed, usage=USAGE)


class FakeActorLLM:
    def __init__(self) -> None:
        self.tiers: list[ModelTier] = []

    def complete_structured_with_usage(self, messages, response_model, *, context=None, tier=ModelTier.FAST, **_):
        assert response_model is EstimationResult and context
        self.tiers.append(tier)
        return done(ACTOR_RESULT)


class FakeCritic:
    def __init__(self, verdict: CriticVerdict | None) -> None:
        self.verdict = verdict  # None -> falla
        self.calls = 0

    def review(self, snapshot, estimate, tier):
        self.calls += 1
        if self.verdict is None:
            raise TimeoutError("critic caído")
        issues = [] if self.verdict is CriticVerdict.OK else [{"category": "missing_phase", "description": "Falta QA."}]
        return done(CriticReport.model_validate({"verdict": self.verdict, "issues": issues}))


class FakeBoss:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.tiers: list[ModelTier] = []

    def arbitrate(self, snapshot, estimate, report, tier):
        self.tiers.append(tier)
        if self.fail:
            raise TimeoutError("boss caído")
        return done(BOSS_RESULT)


def engine(critic: FakeCritic, boss: FakeBoss, llm=None) -> EstimationEngine:
    return EstimationEngine(llm or FakeActorLLM(), critic, boss, min_confidence_pct=30)


def run(eng: EstimationEngine, mode: EstimationMode, tier=ModelTier.FAST):
    return eng.estimate([{"role": "user", "content": "x"}], tier=tier, mode=mode, snapshot=SNAPSHOT)


# ---------------------------------------------------------------- motor ACB


def test_actor_mode_makes_a_single_call_with_the_resolved_tier():
    llm, critic = FakeActorLLM(), FakeCritic(CriticVerdict.REVISE)
    outcome = run(engine(critic, FakeBoss(), llm), EstimationMode.ACTOR, ModelTier.STRONG)
    assert outcome.result is ACTOR_RESULT and outcome.review is None
    assert [c.role for c in outcome.calls] == [CallRole.ACTOR]
    assert llm.tiers == [ModelTier.STRONG] and critic.calls == 0


def test_acb_with_ok_verdict_skips_the_boss():
    boss = FakeBoss()
    outcome = run(engine(FakeCritic(CriticVerdict.OK), boss), EstimationMode.ACB)
    assert outcome.result is ACTOR_RESULT
    assert [c.role for c in outcome.calls] == [CallRole.ACTOR, CallRole.CRITIC]
    assert outcome.review.verdict is CriticVerdict.OK and not outcome.review.revised
    assert boss.tiers == []


def test_acb_with_revise_verdict_returns_the_boss_estimate_on_the_strong_tier():
    boss = FakeBoss()
    outcome = run(engine(FakeCritic(CriticVerdict.REVISE), boss), EstimationMode.ACB)
    assert outcome.result is BOSS_RESULT
    assert [c.role for c in outcome.calls] == [CallRole.ACTOR, CallRole.CRITIC, CallRole.BOSS]
    assert outcome.review.revised and outcome.review.issues == 1
    assert boss.tiers == [ModelTier.STRONG]


@pytest.mark.parametrize(
    ("critic", "boss", "failed_step", "roles"),
    [
        (FakeCritic(None), FakeBoss(), "critic", [CallRole.ACTOR]),
        (FakeCritic(CriticVerdict.REVISE), FakeBoss(fail=True), "boss", [CallRole.ACTOR, CallRole.CRITIC]),
    ],
)
def test_review_failures_keep_the_actor_estimate(critic, boss, failed_step, roles):
    outcome = run(engine(critic, boss), EstimationMode.ACB)
    assert outcome.result is ACTOR_RESULT
    assert outcome.review.failed_step == failed_step and not outcome.review.revised
    assert [c.role for c in outcome.calls] == roles


# --------------------------------------------------------- mantenimiento


class FakeSummarizer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def summarize(self, previous_summary, evicted):
        self.calls.append((previous_summary, evicted.number))
        if self.fail:
            raise TimeoutError("summarizer caído")
        return done(ConversationSummary(summary=f"{previous_summary} +t{evicted.number}".strip()))


class FakeExtractor:
    def extract_with_usage(self, current, user_message, assistant_message):
        return done(ProjectMetadata(mentioned_technologies=["React"]))


def record(maintainer, memory, transcript, metadata=ProjectMetadata()):
    return maintainer.update(memory, metadata, transcript=transcript, user_message=f"U:{transcript}", assistant_message="{}")


def test_summarizer_runs_only_when_a_turn_leaves_the_window_and_accumulates():
    summarizer = FakeSummarizer()
    maintainer = MemoryMaintainer(summarizer, FakeExtractor())
    memory = ConversationMemory(max_turns=2, max_anchors=10)

    updates = [record(maintainer, memory, f"Turno {i}. Presupuesto de {i}0.000 €.") for i in range(1, 5)]

    assert [u.evicted_turn for u in updates] == [None, None, 1, 2]
    assert summarizer.calls == [("", 1), ("+t1", 2)]
    assert memory.summary == "+t1 +t2"
    assert [a.turn for a in memory.anchors] == [1, 2]
    assert [c.role for c in updates[2].calls] == [CallRole.SUMMARIZER, CallRole.METADATA_EXTRACTOR]
    assert [c.role for c in updates[0].calls] == [CallRole.METADATA_EXTRACTOR]
    assert updates[-1].metadata.mentioned_technologies == ["React"]


def test_summarizer_failure_keeps_the_previous_summary_and_the_turn():
    maintainer = MemoryMaintainer(FakeSummarizer(fail=True), FakeExtractor())
    memory = ConversationMemory(max_turns=1, max_anchors=10)
    record(maintainer, memory, "Presupuesto de 5.000 €.")
    update = record(maintainer, memory, "Segundo turno.")
    assert memory.summary == "" and len(memory) == 1
    assert update.evicted_turn == 1 and len(memory.anchors) == 1  # las anclas no dependen del LLM
    assert [c.role for c in update.calls] == [CallRole.METADATA_EXTRACTOR]
