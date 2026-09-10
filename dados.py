"""Camada de dados: conecta o DuckDB aos arquivos em arquivos/ e expõe as
consultas usadas pelo painel (painel.py).

Os dados não são copiados para dentro de um banco — o DuckDB lê os
arquivos direto do disco, então os 16GB nunca são duplicados dentro de um
banco relacional.

Duas fontes possíveis para o mesmo dado:
- os .csv originais em arquivos/csv;
- a cópia em Parquet em arquivos/parquet, gerada por csv2parquet.py.

O Parquet é preferido sempre que estiver em dia, porque é ~20x menor e
~75x mais rápido de filtrar; quando não está, a leitura cai de volta no
CSV automaticamente (ver `parquet_em_dia`).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import duckdb

from sistemas import SISTEMAS, Sistema

CSV_DIR = Path("arquivos/csv")
PARQUET_DIR = Path("arquivos/parquet")
MANIFESTO_PATH = PARQUET_DIR / "_manifesto.json"
DICIONARIO_PATH = Path("dicionario_colunas.csv")

# Valor usado pelo painel no seletor de arquivo para dizer "o grupo inteiro".
TODOS_OS_ARQUIVOS = "__todos__"

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


# ---------- camada Parquet (cópia rápida dos CSVs) ----------
#
# Um .parquet é considerado "em dia" quando o manifesto registra, para ele,
# a mesma assinatura (tamanho + data de modificação) que o CSV tem agora.
# Assinatura diferente = o CSV foi reconvertido/substituído desde então, e
# a leitura volta a usar o CSV. Assim a camada Parquet nunca serve dado
# velho: no pior caso ela é ignorada até csv2parquet.py rodar de novo.


def assinatura(path: Path) -> str:
    info = path.stat()
    return f"{info.st_size}:{int(info.st_mtime)}"


def caminho_parquet(csv_path: Path) -> Path:
    return PARQUET_DIR / f"{csv_path.stem}.parquet"


def ler_manifesto() -> dict[str, str]:
    """Mapa nome do CSV -> assinatura do CSV que gerou o .parquet atual."""
    if not MANIFESTO_PATH.exists():
        return {}
    try:
        return json.loads(MANIFESTO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Manifesto ilegível é tratado como "nada convertido": as consultas
        # ficam lentas, mas continuam corretas.
        return {}


def escrever_manifesto(manifesto: dict[str, str]) -> None:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTO_PATH.write_text(json.dumps(manifesto, indent=2, sort_keys=True), encoding="utf-8")


def parquet_em_dia(csv_path: Path, manifesto: dict[str, str] | None = None) -> bool:
    manifesto = ler_manifesto() if manifesto is None else manifesto
    if manifesto.get(csv_path.name) != assinatura(csv_path):
        return False
    return caminho_parquet(csv_path).exists()


def converter_para_parquet(con: duckdb.DuckDBPyConnection, csv_path: Path) -> Path:
    """Grava o CSV como Parquet comprimido (ZSTD).

    Escreve num arquivo temporário e só então renomeia, para que uma
    conversão interrompida (Ctrl+C, falta de disco) não deixe um .parquet
    truncado ocupando o lugar de um bom.
    """
    destino = caminho_parquet(csv_path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    con.execute(
        f"COPY (SELECT * FROM read_csv(['{csv_path.as_posix()}'], {_READ_CSV_OPTS})) "
        f"TO '{temporario.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporario.replace(destino)
    return destino


def status_parquet() -> dict:
    """Cobertura da camada Parquet, para o painel mostrar na barra lateral."""
    manifesto = ler_manifesto()
    csvs = sorted(CSV_DIR.glob("*.csv"))
    em_dia = [p for p in csvs if parquet_em_dia(p, manifesto)]
    return {
        "total": len(csvs),
        "convertidos": len(em_dia),
        "bytes_csv": sum(p.stat().st_size for p in csvs),
        "bytes_parquet": sum(caminho_parquet(p).stat().st_size for p in em_dia),
    }


# ---------- leitura (Parquet quando dá, CSV quando não) ----------


def _lista_sql(paths: list[Path]) -> str:
    return ", ".join(f"'{p.as_posix()}'" for p in paths)


def _fonte_expr(paths: list[Path]) -> str:
    """Expressão de FROM que lê `paths` da melhor fonte disponível.

    No meio de uma conversão (parte dos arquivos já em Parquet, parte não)
    os dois lados são unidos por nome de coluna — as duas leituras são
    all-varchar, então os tipos batem.
    """
    if not paths:
        raise ValueError("Nenhum arquivo para consultar.")

    manifesto = ler_manifesto()
    parquets: list[Path] = []
    csvs: list[Path] = []
    for path in paths:
        if parquet_em_dia(path, manifesto):
            parquets.append(caminho_parquet(path))
        else:
            csvs.append(path)

    partes = []
    if parquets:
        partes.append(f"SELECT * FROM read_parquet([{_lista_sql(parquets)}], union_by_name=true)")
    if csvs:
        partes.append(f"SELECT * FROM read_csv([{_lista_sql(csvs)}], {_READ_CSV_OPTS})")
    return "(" + " UNION ALL BY NAME ".join(partes) + ")"


def fingerprint(paths: list[Path]) -> str:
    """Identidade do conjunto de dados lido, usada para invalidar o cache
    de resultados (cache.py).

    Usa a assinatura dos CSVs, não dos Parquets: converter para Parquet não
    muda nenhum valor, então um resultado já calculado continua válido
    depois da conversão e não precisa ser recalculado.
    """
    partes = [f"{p.name}:{assinatura(p)}" for p in sorted(paths)]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]


# ---------- quais arquivos cada consulta lê (para o fingerprint) ----------


def paths_do_grupo(grupo: str, arquivo: str) -> list[Path]:
    """Arquivos de um grupo; `arquivo == TODOS_OS_ARQUIVOS` pega o grupo todo."""
    do_grupo = listar_arquivos().get(grupo, [])
    if arquivo == TODOS_OS_ARQUIVOS:
        return do_grupo
    return [p for p in do_grupo if p.name == arquivo]


def paths_cruzamento(campo: str) -> list[Path]:
    grupos = listar_arquivos()
    return [
        path
        for s in SISTEMAS
        if getattr(s, campo) is not None
        for path in grupos.get(s.prefixo, [])
    ]


def paths_detalhe_cnes() -> list[Path]:
    grupos = listar_arquivos()
    return [
        path
        for s in SISTEMAS
        if s.coluna_cnes is not None and s.coluna_procedimento is not None
        for path in grupos.get(s.prefixo, [])
    ]


def colunas(con: duckdb.DuckDBPyConnection, paths: list[Path]) -> list[str]:
    expr = _fonte_expr(paths)
    return [c[0] for c in con.execute(f"SELECT * FROM {expr} LIMIT 0").description]


def schema_e_estatisticas(
    con: duckdb.DuckDBPyConnection, paths: list[Path], dicionario: dict[str, str]
) -> tuple[int, list[dict]]:
    """Para cada coluna do arquivo/grupo: nome real (se houver no dicionário),
    % preenchido, nº aproximado de valores distintos e um exemplo de valor.

    Roda em uma única passada pelo dado (uma consulta agregada só) em vez de
    uma consulta por coluna, senão o custo escala muito com o nº de colunas.
    """
    expr = _fonte_expr(paths)
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
    expr = _fonte_expr(paths)
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
        expr = _fonte_expr(paths)
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
            expr = _fonte_expr([path])
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
