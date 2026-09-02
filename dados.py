"""Camada de dados: conecta o DuckDB aos CSVs em arquivos/csv e expõe as
consultas usadas pelo painel (painel.py).

Os arquivos não são copiados para dentro de um banco — o DuckDB lê os .csv
direto do disco, então os 2.7GB de dados nunca são duplicados.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import duckdb

from sistemas import SISTEMAS, Sistema

CSV_DIR = Path("arquivos/csv")
DICIONARIO_PATH = Path("dicionario_colunas.csv")

# Opções de leitura tolerantes ao CSV do DATASUS: alguns campos numéricos
# usam vírgula como separador decimal sem escapar corretamente, o que
# quebra o parser estrito do DuckDB em algumas linhas.
_READ_CSV_OPTS = "union_by_name=true, all_varchar=true, strict_mode=false, null_padding=true, ignore_errors=true"


def conectar() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA disable_progress_bar")
    return con


def listar_arquivos() -> dict[str, list[Path]]:
    """Agrupa os CSVs existentes por sistema (prefixo do nome do arquivo)."""
    grupos: dict[str, list[Path]] = {}
    for path in sorted(CSV_DIR.glob("*.csv")):
        prefixo = path.stem.rstrip("0123456789")
        grupos.setdefault(prefixo, []).append(path)
    return grupos


def carregar_dicionario(path: Path = DICIONARIO_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {row["coluna"]: row["descricao"] for row in csv.DictReader(f)}


_PADRAO_COMPETENCIA = re.compile(r"(\d{2})(\d{2})$")


def competencia_do_arquivo(nome_arquivo: str) -> str:
    """Extrai a competência (AAAA-MM) do nome do arquivo, ex.:
    'ADBA2601.csv' -> '2026-01'. Os arquivos do DATASUS seguem o padrão
    PREFIXO + AAMM, então isso funciona igual pra todos os sistemas, mesmo
    os que não têm uma coluna de competência própria (ex.: PSBA, ERBA)."""
    match = _PADRAO_COMPETENCIA.search(Path(nome_arquivo).stem)
    if not match:
        return ""
    ano, mes = match.groups()
    return f"20{ano}-{mes}"


def _read_csv_expr(paths: list[Path]) -> str:
    lista = ", ".join(f"'{p.as_posix()}'" for p in paths)
    return f"read_csv([{lista}], {_READ_CSV_OPTS})"


def colunas(con: duckdb.DuckDBPyConnection, paths: list[Path]) -> list[str]:
    expr = _read_csv_expr(paths)
    return [c[0] for c in con.execute(f"SELECT * FROM {expr} LIMIT 0").description]


def schema_e_estatisticas(
    con: duckdb.DuckDBPyConnection, paths: list[Path], dicionario: dict[str, str]
) -> tuple[int, list[dict]]:
    """Para cada coluna do arquivo/grupo: nome real (se houver no dicionário),
    % preenchido, nº aproximado de valores distintos e um exemplo de valor.

    Roda em uma única passada pelo dado (uma consulta agregada só) em vez de
    uma consulta por coluna, senão o custo escala muito com o nº de colunas.
    """
    expr = _read_csv_expr(paths)
    cols = colunas(con, paths)

    selects = ["COUNT(*) AS total_geral"]
    for c in cols:
        cq = c.replace('"', '""')
        selects.append(f'COUNT("{cq}") AS "{cq}__preenchidos"')
        selects.append(f'approx_count_distinct("{cq}") AS "{cq}__distintos"')
        selects.append(
            f'ANY_VALUE("{cq}") FILTER ("{cq}" IS NOT NULL AND "{cq}" <> \'\') AS "{cq}__exemplo"'
        )

    query = f"SELECT {', '.join(selects)} FROM {expr}"
    row = con.execute(query).fetchone()
    header = [d[0] for d in con.description]
    valores = dict(zip(header, row))

    total = valores["total_geral"]
    resultado = []
    for c in cols:
        preenchidos = valores[f"{c}__preenchidos"]
        resultado.append(
            {
                "coluna": c,
                "nome_real": dicionario.get(c, ""),
                "% preenchido": round(100 * preenchidos / total, 1) if total else 0.0,
                "distintos (aprox.)": valores[f"{c}__distintos"],
                "exemplo": valores[f"{c}__exemplo"],
            }
        )
    return total, resultado


def amostra(con: duckdb.DuckDBPyConnection, paths: list[Path], limite: int, offset: int) -> list[dict]:
    expr = _read_csv_expr(paths)
    return con.execute(f"SELECT * FROM {expr} LIMIT {limite} OFFSET {offset}").fetchdf().to_dict(orient="records")


# ---------- cruzamento entre sistemas (CNES / procedimento) ----------


def _uniao_por_campo(sistemas: list[Sistema], campo: str) -> str | None:
    """Monta um SELECT ... UNION ALL que normaliza, para cada sistema que
    tenha o campo (cnes ou procedimento), (sistema, valor)."""
    partes = []
    for s in sistemas:
        coluna = getattr(s, campo)
        if coluna is None:
            continue
        paths = listar_arquivos().get(s.prefixo, [])
        if not paths:
            continue
        expr = _read_csv_expr(paths)
        partes.append(
            f"SELECT DISTINCT '{s.prefixo}' AS sistema, \"{coluna}\" AS valor FROM {expr} "
            f"WHERE \"{coluna}\" IS NOT NULL AND \"{coluna}\" <> ''"
        )
    if not partes:
        return None
    return " UNION ALL ".join(partes)


def cruzamento(con: duckdb.DuckDBPyConnection, campo: str) -> dict:
    """campo: 'coluna_cnes' ou 'coluna_procedimento'.

    Retorna: resumo (nº distintos totais, nº compartilhados), tabela dos
    valores que aparecem em mais de um sistema, e matriz de sobreposição
    par a par entre sistemas.
    """
    uniao = _uniao_por_campo(SISTEMAS, campo)
    if uniao is None:
        return {"total_distintos": 0, "compartilhados": [], "matriz": []}

    con.execute(f"CREATE OR REPLACE TEMP VIEW _cruzamento AS {uniao}")

    total_distintos = con.execute("SELECT COUNT(DISTINCT valor) FROM _cruzamento").fetchone()[0]

    compartilhados = con.execute(
        """
        SELECT valor, COUNT(DISTINCT sistema) AS n_sistemas, list(DISTINCT sistema) AS sistemas
        FROM _cruzamento
        GROUP BY valor
        HAVING COUNT(DISTINCT sistema) > 1
        ORDER BY n_sistemas DESC, valor
        LIMIT 500
        """
    ).fetchdf().to_dict(orient="records")

    matriz = con.execute(
        """
        SELECT a.sistema AS sistema_a, b.sistema AS sistema_b, COUNT(DISTINCT a.valor) AS comuns
        FROM _cruzamento a
        JOIN _cruzamento b ON a.valor = b.valor AND a.sistema < b.sistema
        GROUP BY a.sistema, b.sistema
        """
    ).fetchdf().to_dict(orient="records")

    return {"total_distintos": total_distintos, "compartilhados": compartilhados, "matriz": matriz}


# ---------- detalhe por CNES ----------


def detalhe_cnes(con: duckdb.DuckDBPyConnection, cnes: str) -> list[dict]:
    """Para um CNES específico: em cada arquivo onde ele aparece, os
    procedimentos encontrados, quantas vezes e em qual competência.

    Uma consulta só (UNION ALL de um SELECT por arquivo, cada um já
    filtrado pelo CNES e agrupado por procedimento) — o DuckDB executa os
    ramos em paralelo, então não é um "loop" lento de N consultas."""
    grupos = listar_arquivos()
    partes = []
    parametros: list[str] = []
    for sistema in SISTEMAS:
        if sistema.coluna_cnes is None or sistema.coluna_procedimento is None:
            continue
        for path in grupos.get(sistema.prefixo, []):
            expr = _read_csv_expr([path])
            competencia = competencia_do_arquivo(path.name)
            partes.append(
                f"SELECT '{sistema.prefixo}' AS sistema, '{path.name}' AS arquivo, "
                f"'{competencia}' AS competencia, \"{sistema.coluna_procedimento}\" AS procedimento, "
                f"COUNT(*) AS quantidade "
                f"FROM {expr} WHERE \"{sistema.coluna_cnes}\" = ? "
                f"GROUP BY \"{sistema.coluna_procedimento}\""
            )
            parametros.append(cnes)

    if not partes:
        return []

    query = " UNION ALL ".join(partes) + " ORDER BY procedimento, competencia, arquivo"
    return con.execute(query, parametros).fetchdf().to_dict(orient="records")
