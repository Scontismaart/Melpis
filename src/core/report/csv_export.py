"""Export CSV prenotazioni completate per commercialista.

Formato minimo: data, ora, coperti, nome_cliente, stato.
Encoding UTF-8 con BOM per apertura diretta in Excel.
"""

import csv
import io
from datetime import date
from src.core.bookings.provenance_quarantine import quarantine_predicate

# BOM UTF-8 per Excel
_BOM = "\ufeff"

# Colonne del CSV
_COLONNE = ["data", "ora", "coperti", "nome_cliente", "stato"]


async def get_prenotazioni_completate(
    pool,
    org_id: str,
    inizio: date,
    fine: date,
    stati: list[str] | None = None,
) -> list[dict]:
    """Recupera le prenotazioni nel periodo per l'organizzazione.

    Filtra per data del servizio (campo `data`), non per created_at.
    Per default include solo prenotazioni 'completata' (contabilità).
    Per export pre-servizio usare stati=['confermata'] o ['confermata','completata'].
    """
    if stati is None:
        stati = ["completata"]
    if not stati:
        return []

    async with pool.acquire() as conn:
        visible = await quarantine_predicate(conn)
        rows = await conn.fetch(f"""
            SELECT data, ora, coperti, nome_cliente, stato
            FROM bookings
            WHERE organization_id = $1
              AND data >= $2
              AND data <= $3
              AND stato = ANY($4::text[])
              AND {visible}
            ORDER BY data, ora
        """, org_id, inizio, fine, stati)
    return [dict(r) for r in rows]


def genera_csv(prenotazioni: list[dict]) -> bytes:
    """Genera il contenuto CSV come bytes UTF-8 con BOM.

    Restituisce bytes pronti per essere allegati a un'email o serviti
    come download da un endpoint API.
    """
    buf = io.StringIO()
    buf.write(_BOM)

    writer = csv.DictWriter(buf, fieldnames=_COLONNE, extrasaction="ignore")
    writer.writeheader()

    for p in prenotazioni:
        # Converte date/time objects in stringhe leggibili
        row = {}
        for col in _COLONNE:
            val = p.get(col, "")
            if isinstance(val, date) or hasattr(val, "isoformat"):
                val = val.isoformat()
            row[col] = val if val is not None else ""
        writer.writerow(row)

    return buf.getvalue().encode("utf-8")
