"""Detección y redacción de PII (funciones puras)."""

import pytest

from app.guardrails.normalize import normalize
from app.guardrails.pii import REDACTED, detect_pii, find_pii, iban_is_valid, redact

VALID_IBAN = "ES91 2100 0418 4502 0005 1332"
INVALID_IBAN = "ES91 2100 0418 4502 0005 1333"  # último dígito cambiado


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Escribid a ana.lopez@empresa.es para dudas", "email"),
        ("Contacto: 612 345 678", "phone"),
        ("Contacto: +34 612-345-678", "phone"),
        ("Oficina 91 123 45 67", "phone"),
        ("Soporte en +44 20 7946 0958", "phone"),
        (f"Cobrar en {VALID_IBAN} cada mes", "iban"),
        ("Cuenta GB82WEST12345698765432.", "iban"),
    ],
)
def test_detects_email_phone_and_valid_iban(text, category):
    assert [m.category for m in find_pii(text)] == [category]


def test_iban_with_invalid_checksum_is_not_pii():
    assert not iban_is_valid(INVALID_IBAN)
    assert find_pii(f"Cuenta {INVALID_IBAN}") == []
    assert iban_is_valid(VALID_IBAN)


@pytest.mark.parametrize(
    "text",
    [
        "Portal para 1200 usuarios con 3 sedes y 45 salas.",
        "Presupuesto máximo de 150000 euros en 2026.",
        "Versión 2.4.1 de la API REST",
    ],
)
def test_normal_numbers_are_not_pii(text):
    assert find_pii(text) == []


def test_detection_works_on_normalized_text():
    # Dígitos de ancho completo: la normalización NFKC los convierte.
    assert detect_pii(normalize("Tel ６１２ ３４５ ６７８")).triggered


def test_detect_reports_only_categories():
    result = detect_pii(f"ana@empresa.es, {VALID_IBAN}")
    assert result.triggered
    assert result.internal_detail == "email,iban"
    assert "ana" not in result.internal_detail


def test_redact_replaces_every_match():
    text, categories = redact(f"Mail ana@empresa.es, IBAN {VALID_IBAN} y tel 612345678.")
    assert text == f"Mail {REDACTED}, IBAN {REDACTED} y tel {REDACTED}."
    assert categories == ["email", "iban", "phone"]
    assert redact("Sin datos personales") == ("Sin datos personales", [])
