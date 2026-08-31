"""Converte todos os arquivos .dbc de arquivos/dbc para .csv em arquivos/csv.

Sem pandas, sem pysus: usa dbctodbf (puro Python) só para descomprimir o
.dbc em .dbf, e struct/csv da stdlib para o resto.
"""
import csv
import struct
from pathlib import Path

from dbctodbf import DBCDecompress

DBC_DIR = Path("arquivos/dbc")
CSV_DIR = Path("arquivos/csv")
ENCODING = "latin-1"

FIELD_TERMINATOR = 0x0D
DELETED_FLAG = 0x2A

# Alguns campos numéricos longos (ex.: AP_CNSPCN) o DATASUS grava com
# dígito = byte - 123 em vez de ASCII normal. Detecta pelo padrão de bytes.
PACKED_DIGIT_MIN = 0x7B
PACKED_DIGIT_MAX = 0x84


def decode_field(raw: bytes) -> str:
    if raw and all(PACKED_DIGIT_MIN <= b <= PACKED_DIGIT_MAX for b in raw):
        return "".join(str(b - PACKED_DIGIT_MIN) for b in raw)
    return raw.decode(ENCODING).strip()


def parse_dbf(data: bytes) -> tuple[list[str], list[list[str]]]:
    num_records, header_size, record_length = struct.unpack_from("<IHH", data, 4)

    fields = []
    offset = 32
    while data[offset] != FIELD_TERMINATOR:
        name = data[offset : offset + 11].split(b"\x00")[0].decode(ENCODING)
        length = data[offset + 16]
        fields.append((name, length))
        offset += 32

    header_names = [name for name, _ in fields]

    rows = []
    offset = header_size
    for _ in range(num_records):
        record = data[offset : offset + record_length]
        offset += record_length
        if not record or record[0] == DELETED_FLAG:
            continue

        row = []
        pos = 1  # pula o byte de flag de exclusão
        for _name, length in fields:
            raw = record[pos : pos + length]
            row.append(decode_field(raw))
            pos += length
        rows.append(row)

    return header_names, rows


def convert_dbc_to_csv(dbc_path: Path, csv_path: Path) -> None:
    dbc_bytes = dbc_path.read_bytes()
    dbf_bytes = DBCDecompress().decompress(dbc_bytes)
    header, rows = parse_dbf(dbf_bytes)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    dbc_files = sorted(DBC_DIR.glob("*.dbc"))
    if not dbc_files:
        print(f"Nenhum arquivo .dbc encontrado em {DBC_DIR}")
        return

    ok, falhas = 0, 0
    for dbc_path in dbc_files:
        csv_path = CSV_DIR / f"{dbc_path.stem}.csv"
        try:
            convert_dbc_to_csv(dbc_path, csv_path)
            print(f"OK   {dbc_path.name} -> {csv_path.name}")
            ok += 1
        except Exception as exc:
            print(f"FALHA {dbc_path.name}: {exc}")
            falhas += 1

    print(f"\nConcluído: {ok} convertido(s), {falhas} falha(s).")


if __name__ == "__main__":
    main()
