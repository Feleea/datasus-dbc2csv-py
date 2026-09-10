"""Gera e mantém a cópia em Parquet dos CSVs de arquivos/csv.

Por quê: o painel consulta os arquivos direto do disco, e são ~16GB de CSV
(13GB só de PABA) — cada busca reparseia tudo de novo. Os mesmos dados em
Parquet ocupam ~20x menos espaço e filtram ~75x mais rápido, então uma
busca de CNES em todos os sistemas cai de ~1 minuto para ~1 segundo.

A execução é incremental: compara o tamanho e a data de modificação de
cada CSV com o que está registrado no manifesto e converte só o que falta
ou mudou. Rodar de novo depois de converter DBCs novos leva segundos se
nada mais mudou.

Uso:
    python csv2parquet.py            # converte o que falta ou mudou
    python csv2parquet.py --forcar   # reconverte tudo do zero
    python csv2parquet.py --limpar   # apaga a camada Parquet inteira
"""
from __future__ import annotations

import argparse
import shutil
import time

import dados


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f}GB"


def limpar() -> None:
    if not dados.PARQUET_DIR.exists():
        print(f"Nada a apagar: {dados.PARQUET_DIR} não existe.")
        return
    liberado = sum(p.stat().st_size for p in dados.PARQUET_DIR.glob("*.parquet"))
    shutil.rmtree(dados.PARQUET_DIR)
    print(f"Camada Parquet apagada ({_gb(liberado)} liberados).")
    print("O painel volta a ler os CSVs — correto, só mais lento.")


def _remover_orfaos(manifesto: dict[str, str]) -> int:
    """Apaga .parquet cujo CSV de origem não existe mais."""
    removidos = 0
    for parquet_path in dados.PARQUET_DIR.glob("*.parquet"):
        csv_path = dados.CSV_DIR / f"{parquet_path.stem}.csv"
        if csv_path.exists():
            continue
        parquet_path.unlink()
        manifesto.pop(csv_path.name, None)
        removidos += 1
        print(f"REMOVIDO {parquet_path.name} (CSV de origem não existe mais)")
    # Entradas do manifesto sem CSV e sem .parquet também saem.
    for nome in [n for n in manifesto if not (dados.CSV_DIR / n).exists()]:
        manifesto.pop(nome)
    return removidos


def converter(forcar: bool = False) -> None:
    csvs = sorted(dados.CSV_DIR.glob("*.csv"))
    if not csvs:
        print(f"Nenhum arquivo .csv encontrado em {dados.CSV_DIR}")
        return

    dados.PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    manifesto = {} if forcar else dados.ler_manifesto()
    removidos = _remover_orfaos(manifesto)

    pendentes = [p for p in csvs if forcar or not dados.parquet_em_dia(p, manifesto)]
    if not pendentes:
        print(f"Tudo em dia: {len(csvs)} arquivo(s) já convertido(s).")
        if removidos:
            dados.escrever_manifesto(manifesto)
        return

    print(f"{len(pendentes)} de {len(csvs)} arquivo(s) a converter ({_gb(sum(p.stat().st_size for p in pendentes))}).")

    con = dados.conectar()
    ok, falhas = 0, 0
    total = len(pendentes)
    inicio_total = time.perf_counter()
    for i, csv_path in enumerate(pendentes, start=1):
        progresso = f"Convertendo arquivo {i} de {total}: {csv_path.name}"
        print(progresso, end="\r", flush=True)
        inicio = time.perf_counter()
        try:
            # A assinatura é lida ANTES da conversão: se o CSV for
            # reescrito durante ela, a assinatura antiga não vai bater com
            # a do arquivo novo e o .parquet será refeito na próxima
            # execução, em vez de passar por atualizado.
            assinatura = dados.assinatura(csv_path)
            destino = dados.converter_para_parquet(con, csv_path)
            manifesto[csv_path.name] = assinatura
            # Grava o manifesto a cada arquivo: se a execução for
            # interrompida, o que já foi convertido continua valendo.
            dados.escrever_manifesto(manifesto)
            duracao = time.perf_counter() - inicio
            reducao = 100 * (1 - destino.stat().st_size / max(1, csv_path.stat().st_size))
            linha = f"OK   {csv_path.name} -> {destino.name} ({duracao:.1f}s, -{reducao:.0f}%)"
            ok += 1
        except Exception as exc:
            duracao = time.perf_counter() - inicio
            linha = f"FALHA {csv_path.name}: {exc} ({duracao:.1f}s)"
            falhas += 1
        print(linha.ljust(len(progresso)))
    con.close()

    status = dados.status_parquet()
    print(
        f"\nConcluído: {ok} convertido(s), {falhas} falha(s)"
        f"{f', {removidos} removido(s)' if removidos else ''}."
        f" Tempo total: {time.perf_counter() - inicio_total:.1f}s"
    )
    print(
        f"Camada Parquet: {status['convertidos']} de {status['total']} arquivo(s), "
        f"{_gb(status['bytes_parquet'])} (contra {_gb(status['bytes_csv'])} em CSV)."
    )
    if falhas:
        print("Os arquivos que falharam continuam sendo lidos do CSV — o painel segue correto.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--forcar", action="store_true", help="reconverte todos os arquivos, mesmo os já em dia")
    grupo.add_argument("--limpar", action="store_true", help="apaga a camada Parquet inteira e sai")
    args = parser.parse_args()

    if args.limpar:
        limpar()
    else:
        converter(forcar=args.forcar)


if __name__ == "__main__":
    main()
