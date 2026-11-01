"""Dominio conversacional puro: anclas, memoria en tres capas, tiers y uso."""

import pytest
from pydantic import ValidationError

from app.conversation.domain.anchors import MAX_ANCHOR_CHARS, AnchorKind, extract_anchors
from app.conversation.domain.memory import ConversationMemory
from app.conversation.domain.review import CriticReport
from app.conversation.domain.tiering import TierPolicy, TierSignals
from app.conversation.domain.usage import CallRecord, CallRole, SessionTelemetry, TurnUsage
from app.ports.llm import LLMUsage, ModelTier

# ------------------------------------------------------------------- anclas


@pytest.mark.parametrize(
    ("sentence", "kind"),
    [
        ("El presupuesto máximo es de 40.000 €.", AnchorKind.BUDGET),
        ("No podemos pasar de 25k euros.", AnchorKind.BUDGET),
        ("Seremos un equipo de 4 personas.", AnchorKind.TEAM),
        ("Tiene que estar listo en 12 semanas.", AnchorKind.DEADLINE),
        ("Lo necesitamos antes de marzo.", AnchorKind.DEADLINE),
        ("El SSO corporativo es obligatorio.", AnchorKind.CONSTRAINT),
        ("La app de Android no incluye pagos.", AnchorKind.CONSTRAINT),
        ("Hemos decidido usar PostgreSQL.", AnchorKind.TECH_DECISION),
    ],
)
def test_each_rule_detects_its_kind(sentence, kind):
    (anchor,) = extract_anchors(sentence, turn=1)
    assert anchor.kind is kind and anchor.text == sentence and anchor.turn == 1


def test_sentences_without_key_facts_are_not_anchors():
    assert extract_anchors("Hola a todos. Hoy hablamos del portal. Gracias por venir.", turn=1) == ()


def test_anchors_are_deduplicated_and_clipped():
    long = "El presupuesto es " + "muy " * 200 + "ajustado."
    anchors = extract_anchors(f"Presupuesto de 10k. presupuesto  de 10K. {long}", turn=2)
    assert len(anchors) == 2
    assert len(anchors[1].text) == MAX_ANCHOR_CHARS and anchors[1].text.endswith("…")


# ------------------------------------------------------------------ memoria


def _memory(max_turns=2, max_anchors=3) -> ConversationMemory:
    return ConversationMemory(max_turns=max_turns, max_anchors=max_anchors)


def test_anchors_are_promoted_only_when_their_turn_leaves_the_window():
    memory = _memory(max_turns=2)
    first = extract_anchors("Presupuesto de 30.000 €.", turn=memory.next_turn_number)
    assert memory.record_turn("u1", "a1", first) is None
    assert memory.anchors == ()  # dentro de la ventana: ya viaja literal
    memory.record_turn("u2", "a2", ())

    evicted = memory.record_turn("u3", "a3", ())

    assert evicted is not None and evicted.number == 1
    assert [a.text for a in memory.anchors] == ["Presupuesto de 30.000 €."]
    assert len(memory) == 2 and memory.message_count == 4


def test_anchor_list_keeps_the_newest_up_to_the_maximum():
    memory = _memory(max_turns=1, max_anchors=2)
    for i in range(1, 5):
        memory.record_turn(f"u{i}", f"a{i}", extract_anchors(f"Equipo de {i} personas.", turn=i))
    assert [a.turn for a in memory.anchors] == [2, 3]


def test_summary_is_stored_trimmed():
    memory = _memory()
    memory.update_summary("  resumen \n")
    assert memory.summary == "resumen"


# -------------------------------------------------------------------- tiers


def _signals(**overrides) -> TierSignals:
    base = {"transcript": "Un portal sencillo.", "attachment_chars": 0, "previous_confidence_pct": None, "has_summary": False}
    return TierSignals(**(base | overrides))


@pytest.mark.parametrize(
    ("overrides", "tier", "rule"),
    [
        ({}, ModelTier.FAST, "default"),
        ({"attachment_chars": 9000}, ModelTier.STRONG, "large_attachments"),
        ({"transcript": "x" * 7000}, ModelTier.STRONG, "long_transcript"),
        ({"previous_confidence_pct": 40}, ModelTier.STRONG, "low_previous_confidence"),
        ({"previous_confidence_pct": 80}, ModelTier.FAST, "default"),
        ({"transcript": "Hay que hacer la migración del ERP legacy."}, ModelTier.STRONG, "complexity_keywords"),
        ({"has_summary": True}, ModelTier.STRONG, "long_conversation"),
    ],
)
def test_tier_rules(overrides, tier, rule):
    decision = TierPolicy().resolve(_signals(**overrides))
    assert (decision.tier, decision.rule) == (tier, rule)


def test_first_matching_rule_wins():
    decision = TierPolicy().resolve(_signals(attachment_chars=9000, has_summary=True))
    assert decision.rule == "large_attachments"


def test_tier_mode_off_is_always_fast():
    decision = TierPolicy(enabled=False).resolve(_signals(attachment_chars=99_999))
    assert (decision.tier, decision.rule) == (ModelTier.FAST, "tier_mode_off")


# ---------------------------------------------------------- revisión y uso


def test_critic_report_revise_requires_issues():
    with pytest.raises(ValidationError):
        CriticReport.model_validate({"verdict": "revise", "issues": []})
    assert CriticReport.model_validate({"verdict": "ok"}).issues == []


def _usage(cost, tokens_in=10, tokens_out=5, latency=2.0) -> LLMUsage:
    return LLMUsage("m", "p", latency, tokens_in, tokens_out, cost)


def test_turn_usage_sums_and_ignores_unknown_costs():
    usage = TurnUsage((
        CallRecord(CallRole.ACTOR, _usage(0.01)),
        CallRecord(CallRole.CRITIC, _usage(None)),
        CallRecord(CallRole.METADATA_EXTRACTOR, _usage(0.002)),
    ))
    assert usage.cost_usd == 0.012
    assert (usage.tokens_in, usage.tokens_out, usage.latency_ms) == (30, 15, 6.0)
    assert usage.count_by_role() == {"actor": 1, "critic": 1, "metadata_extractor": 1}


def test_session_telemetry_accumulates_turns():
    telemetry = SessionTelemetry()
    decision = TierPolicy().resolve(_signals())
    telemetry.record(decision, TurnUsage((CallRecord(CallRole.ACTOR, _usage(0.01)),)))
    telemetry.record(decision, TurnUsage((CallRecord(CallRole.ACTOR, _usage(0.02)),)))
    assert telemetry.total_cost_usd == 0.03 and telemetry.total_tokens_in == 20
    assert telemetry.last_tier == decision and telemetry.last_turn.cost_usd == 0.02
