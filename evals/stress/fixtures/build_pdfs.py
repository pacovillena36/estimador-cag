"""PDFs sintéticos de tamaño calibrado para el stress test del CAG.

    uv run python -m evals.stress.fixtures.build_pdfs

Genera attach_{5,20,50,100}kb.pdf en evals/stress/fixtures/out/ (ignorado
por git) y un manifest.json con los canarios de cada fichero.

- Se calibra por CARACTERES DE TEXTO EXTRAÍDO (5 000 / 20 000 / 50 000 /
  100 000), no por bytes: el límite MAX_ATTACHMENT_CHARS actúa sobre texto.
  El tamaño se verifica con el mismo extractor de la app
  (app.attachments.extract_pdf_text) y falla si se desvía más de ±5 %.
- Canarios: tokens únicos y deterministas (CANARY-START/MID/END-…) y, en los
  ficheros que superan MAX_ATTACHMENT_CHARS, un CANARY-AFTER-CAP-… situado
  justo después del límite. Sus offsets en el texto extraído quedan en el
  manifest: comparándolos con attachments_total_chars del turno se ve
  exactamente dónde trunca la app.
- Deterministas: texto sin aleatoriedad y fecha de creación fija (el /ID del
  PDF se deriva del contenido), así dos ejecuciones producen los mismos
  bytes. Contenido ficticio y solo ASCII (las fuentes core de PDF son
  latin-1).
"""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF

from app.attachments import extract_pdf_text
from app.config import get_settings

OUT_DIR = Path(__file__).resolve().parent / "out"
MANIFEST = "manifest.json"
SIZES_KB: tuple[int, ...] = (5, 20, 50, 100)
TOLERANCE = 0.05
_FIXED_DATE = datetime(2000, 1, 1, tzinfo=UTC)

_TOPICS = (
    "gestion de inventario",
    "panel de administracion",
    "informes mensuales",
    "control de accesos",
    "notificaciones por correo",
    "exportacion de datos",
    "registro de actividad",
    "busqueda avanzada",
)


@dataclass(frozen=True)
class Canary:
    label: str  # START | MID | END | AFTER-CAP
    token: str
    offset: int  # posición en el texto extraído (calculada tras extraer)


@dataclass(frozen=True)
class FixtureInfo:
    filename: str
    size_kb: int
    target_chars: int
    extracted_chars: int
    cap_chars: int  # MAX_ATTACHMENT_CHARS en el momento de generar
    canaries: tuple[Canary, ...]


def canary_token(size_kb: int, label: str) -> str:
    digest = hashlib.sha256(f"stress-{size_kb}-{label}".encode()).hexdigest()[:4].upper()
    return f"CANARY-{label}-{digest}"


def _filler_line(index: int) -> str:
    topic = _TOPICS[index % len(_TOPICS)]
    return (
        f"Requisito {index:05d}: el modulo de {topic} debe cumplir el criterio de aceptacion "
        f"numero {index % 97} acordado con el cliente ficticio."
    )


def _canary_line(token: str) -> str:
    return f"Requisito clave {token}: este identificador debe conservarse en la estimacion."


def _plan(size_kb: int, target: int, cap: int) -> list[tuple[float, str]]:
    """(fracción del objetivo, etiqueta) donde se inserta cada canario."""
    plan = [(0.0, "START"), (0.5, "MID"), (0.97, "END")]
    if target > cap:
        plan.append(((cap + 300) / target, "AFTER-CAP"))
    return sorted(plan)


def _lines(size_kb: int, target: int, cap: int, filler_count: int) -> list[str]:
    plan = _plan(size_kb, target, cap)
    lines: list[str] = []
    chars = 0
    pending = list(plan)
    index = 0
    while index < filler_count:
        while pending and chars >= pending[0][0] * target:
            line = _canary_line(canary_token(size_kb, pending.pop(0)[1]))
            lines.append(line)
            chars += len(line) + 1
        line = _filler_line(index)
        lines.append(line)
        chars += len(line) + 1
        index += 1
    for _, label in pending:  # por si el relleno se quedó corto
        lines.append(_canary_line(canary_token(size_kb, label)))
    return lines


def _render(lines: list[str]) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_creation_date(_FIXED_DATE)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()
    pdf.set_font("Helvetica", size=8)
    for line in lines:
        pdf.multi_cell(0, 3.6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _build_one(size_kb: int, cap: int, max_pages: int) -> tuple[bytes, FixtureInfo]:
    target = size_kb * 1000
    labels = [label for _, label in _plan(size_kb, target, cap)]
    # Ajuste del nº de líneas de relleno hasta caer en ±1 % del objetivo
    # (la extracción cambia espacios y saltos de línea respecto al origen).
    count = max(1, target // len(_filler_line(0)))
    for _ in range(12):
        data = _render(_lines(size_kb, target, cap, count))
        text = extract_pdf_text(data, max_pages=max_pages)
        error = (len(text) - target) / target
        if abs(error) <= 0.01:
            break
        count = max(1, round(count * target / len(text)))
    if abs(error) > TOLERANCE:
        raise RuntimeError(f"attach_{size_kb}kb: {len(text)} caracteres extraídos, fuera de ±5 % de {target}")
    canaries = []
    for label in labels:
        token = canary_token(size_kb, label)
        offset = text.find(token)
        if offset < 0:
            raise RuntimeError(f"attach_{size_kb}kb: el canario {token} no aparece en el texto extraído")
        canaries.append(Canary(label, token, offset))
    info = FixtureInfo(f"attach_{size_kb}kb.pdf", size_kb, target, len(text), cap, tuple(canaries))
    return data, info


def build_all(out_dir: Path = OUT_DIR) -> list[FixtureInfo]:
    settings = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    infos: list[FixtureInfo] = []
    for size_kb in SIZES_KB:
        data, info = _build_one(size_kb, settings.max_attachment_chars, settings.max_pdf_pages)
        (out_dir / info.filename).write_bytes(data)
        infos.append(info)
    manifest = [asdict(info) for info in infos]
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return infos


def load_manifest(out_dir: Path = OUT_DIR) -> dict[int, FixtureInfo]:
    """Manifest de los fixtures; los regenera si falta algún fichero."""
    files = [out_dir / f"attach_{kb}kb.pdf" for kb in SIZES_KB] + [out_dir / MANIFEST]
    if not all(f.exists() for f in files):
        build_all(out_dir)
    raw = json.loads((out_dir / MANIFEST).read_text(encoding="utf-8"))
    infos = [FixtureInfo(**{**r, "canaries": tuple(Canary(**c) for c in r["canaries"])}) for r in raw]
    return {info.size_kb: info for info in infos}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Genera los PDFs calibrados del stress test").parse_args(argv)
    try:
        infos = build_all()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{'fichero':<20}{'objetivo':>10}{'extraído':>10}{'desv.':>8}  canarios (offset)")
    for info in infos:
        deviation = (info.extracted_chars - info.target_chars) / info.target_chars
        canaries = ", ".join(f"{c.label}@{c.offset}" for c in info.canaries)
        print(f"{info.filename:<20}{info.target_chars:>10}{info.extracted_chars:>10}{deviation:>+8.1%}  {canaries}")
    print(f"\nMAX_ATTACHMENT_CHARS={infos[0].cap_chars} · salida en {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
