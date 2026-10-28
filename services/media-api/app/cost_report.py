"""Small, dependency-free PDF report for the API usage dashboard."""

from datetime import datetime, timezone
import textwrap


MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
TYPE_LABELS = {
    "actual": "Real",
    "estimated": "Estimado",
    "local": "Registro local",
    "unavailable": "No disponible",
}


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    formatted = f"{float(value):,.4f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} US$"


def _period_label(period: str) -> str:
    try:
        year, month = (int(part) for part in period.split("-", 1))
        return f"{MONTHS_ES[month - 1].capitalize()} de {year}"
    except (ValueError, IndexError):
        return period


def _updated_label(value: str | None) -> str:
    try:
        updated = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        updated = datetime.now(timezone.utc)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def build_cost_report(costs: dict) -> bytes:
    """Render one A4 page from the public, non-sensitive costs response."""
    commands: list[str] = []

    def text(x: float, y: float, value: str, size: float = 10, bold: bool = False,
             color: tuple[float, float, float] = (0.12, 0.16, 0.23)) -> None:
        font = "F2" if bold else "F1"
        commands.append(
            f"{color[0]} {color[1]} {color[2]} rg BT /{font} {size} Tf "
            f"1 0 0 1 {x} {y} Tm ({_escape(value)}) Tj ET"
        )

    commands.extend([
        "0.09 0.14 0.22 rg 0 720 595 122 re f",
        "0.14 0.30 0.80 rg 42 690 62 4 re f",
    ])
    text(42, 786, "Informe de uso de APIs", 23, True, (1, 1, 1))
    text(42, 761, _period_label(str(costs.get("period", ""))), 11, False, (0.82, 0.86, 0.92))
    text(385, 761, f"Actualizado {_updated_label(costs.get('refreshed_at'))}", 8, False,
         (0.82, 0.86, 0.92))

    text(42, 665, "Coste conocido", 10, False, (0.35, 0.40, 0.48))
    text(42, 626, _money(costs.get("total_cost_usd")), 29, True)
    split = (
        f"{_money(costs.get('actual_cost_usd'))} real  |  "
        f"{_money(costs.get('estimated_cost_usd'))} estimado"
    )
    text(42, 604, split, 10, False, (0.35, 0.40, 0.48))

    text(42, 560, "Proveedor", 9, True, (0.35, 0.40, 0.48))
    text(405, 560, "Tipo", 9, True, (0.35, 0.40, 0.48))
    text(475, 560, "Importe", 9, True, (0.35, 0.40, 0.48))
    commands.append("0.84 0.87 0.91 RG 42 550 m 553 550 l S")

    y = 520
    for provider in costs.get("providers", [])[:5]:
        name = str(provider.get("name") or provider.get("id") or "Proveedor")
        detail = str(provider.get("detail") or "")
        requests = provider.get("requests")
        if requests is not None:
            detail = f"{requests} {'llamada' if requests == 1 else 'llamadas'}  |  {detail}"
        text(42, y, name, 11, True)
        for index, line in enumerate(textwrap.wrap(detail, width=68)[:2]):
            text(42, y - 17 - index * 13, line, 8.5, False, (0.35, 0.40, 0.48))
        text(405, y, TYPE_LABELS.get(str(provider.get("cost_type")), "No disponible"), 9)
        text(475, y, _money(provider.get("cost_usd")), 10.5, True)
        commands.append(f"0.88 0.90 0.93 RG 42 {y - 43} m 553 {y - 43} l S")
        y -= 72

    note_y = max(115, y - 2)
    text(42, note_y, "Notas", 10, True)
    notes = [
        "Los importes reales proceden de los proveedores disponibles.",
        "Replicate se muestra como estimación cuando no publica el cargo final.",
        "Los importes estimados no equivalen a una factura del proveedor.",
    ]
    for index, note in enumerate(notes):
        text(42, note_y - 18 - index * 15, note, 8.5, False, (0.35, 0.40, 0.48))

    text(42, 42, "MedIA  |  Informe generado por el backend sin incluir credenciales.", 8, False,
         (0.45, 0.49, 0.56))
    content = ("\n".join(commands) + "\n").encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
         b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream",
    ]
    return _pdf(objects)
