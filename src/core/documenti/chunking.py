import re


def chunk_testo(testo: str, size: int = 500, overlap: int = 50) -> list[str]:
    if type(size) is not int or size < 1 or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("Invalid chunk limits")
    if not testo.strip():
        return []

    paragrafi = re.split(r"\n\s*\n", testo)
    chunk_corrente = ""
    chunks: list[str] = []

    for par in paragrafi:
        par = par.strip()
        if not par:
            continue

        if len(chunk_corrente) + len(par) + 1 <= size:
            chunk_corrente = (chunk_corrente + "\n" + par).strip()
        else:
            if chunk_corrente:
                chunks.append(chunk_corrente)
            if len(par) > size:
                parole = par.split()
                chunk_corrente = ""
                for parola in parole:
                    if len(chunk_corrente) + len(parola) + 1 > size:
                        chunks.append(chunk_corrente.strip())
                        chunk_corrente = chunk_corrente.split()[-overlap:] if overlap else []
                        chunk_corrente = " ".join(chunk_corrente) + " " if chunk_corrente else ""
                    chunk_corrente += parola + " "
            else:
                chunk_corrente = par

    if chunk_corrente.strip():
        chunks.append(chunk_corrente.strip())

    # Long tokens and word-based overlap can exceed the configured character
    # budget in the legacy splitter. Bound every result, retaining all text.
    bounded = []
    for chunk in chunks:
        chunk = chunk.strip()
        while len(chunk) > size:
            bounded.append(chunk[:size])
            chunk = chunk[max(1, size - overlap):]
        if chunk:
            bounded.append(chunk)
    return bounded
