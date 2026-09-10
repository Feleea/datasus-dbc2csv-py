"""Cache em disco dos resultados das buscas do painel.

O `@st.cache_data` do Streamlit já evita repetir uma consulta dentro da
sessão, mas o resultado vive só na memória do processo: fechou o painel,
perdeu tudo. Aqui cada resultado vai para um JSON em arquivos/cache/,
então uma busca já feita volta instantânea mesmo depois de reiniciar.

Invalidação: cada entrada guarda o fingerprint dos arquivos que a
originaram (`dados.fingerprint`). Se um CSV novo aparece, é reconvertido
ou some, o fingerprint muda, a entrada é ignorada e sobrescrita na próxima
busca — o cache nunca serve resultado velho. Nada aqui é insubstituível:
apagar arquivos/cache/ inteiro só custa recalcular.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

CACHE_DIR = Path("arquivos/cache")

# Entradas mais velhas que isso são descartadas na abertura do painel. Elas
# já seriam invalidadas se o dado mudasse; o prazo existe só para o
# diretório não crescer indefinidamente com buscas pontuais.
VALIDADE_DIAS = 30

# Distingue "não tem no cache" de "tem no cache, e o valor é None".
AUSENTE = object()


def _chave(consulta: str, parametros: dict) -> str:
    corpo = json.dumps(parametros, sort_keys=True, default=str)
    return hashlib.sha256(f"{consulta}|{corpo}".encode()).hexdigest()[:16]


def _caminho(consulta: str, parametros: dict) -> Path:
    return CACHE_DIR / f"{consulta}-{_chave(consulta, parametros)}.json"


def _json_default(valor: Any) -> Any:
    """Os resultados vêm de DataFrames, então trazem escalares e arrays do
    numpy, que o json não serializa. `.tolist()` existe nos dois casos e
    devolve o equivalente nativo do Python."""
    tolist = getattr(valor, "tolist", None)
    if tolist is not None:
        return tolist()
    return str(valor)


def obter(consulta: str, parametros: dict, fingerprint: str) -> Any:
    """Valor guardado para essa busca, ou AUSENTE se não houver um válido."""
    caminho = _caminho(consulta, parametros)
    if not caminho.exists():
        return AUSENTE
    try:
        entrada = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Entrada corrompida (ex.: disco cheio no meio de uma gravação
        # anterior): trata como ausente e deixa ser sobrescrita.
        return AUSENTE
    if entrada.get("fingerprint") != fingerprint:
        return AUSENTE
    return entrada["valor"]


def guardar(consulta: str, parametros: dict, fingerprint: str, valor: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    caminho = _caminho(consulta, parametros)
    entrada = {
        "consulta": consulta,
        "parametros": parametros,
        "fingerprint": fingerprint,
        "criado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "valor": valor,
    }
    # Grava e renomeia, para não deixar um JSON pela metade se a escrita
    # for interrompida.
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(json.dumps(entrada, default=_json_default), encoding="utf-8")
    temporario.replace(caminho)


def memoizar(consulta: str, parametros: dict, fingerprint: str, calcular: Callable[[], Any]) -> Any:
    """Devolve o resultado do cache; se não houver, chama `calcular` e guarda."""
    valor = obter(consulta, parametros, fingerprint)
    if valor is not AUSENTE:
        return valor
    valor = calcular()
    guardar(consulta, parametros, fingerprint, valor)
    return valor


def limpar(consulta: str | None = None) -> int:
    """Apaga as entradas do cache — todas, ou só as de uma consulta.
    Devolve quantas foram apagadas."""
    padrao = "*.json" if consulta is None else f"{consulta}-*.json"
    apagadas = 0
    for caminho in CACHE_DIR.glob(padrao):
        try:
            caminho.unlink()
            apagadas += 1
        except OSError:
            pass
    return apagadas


def descartar_antigos(dias: int = VALIDADE_DIAS) -> int:
    limite = time.time() - dias * 86400
    apagadas = 0
    for caminho in CACHE_DIR.glob("*.json"):
        try:
            if caminho.stat().st_mtime < limite:
                caminho.unlink()
                apagadas += 1
        except OSError:
            pass
    return apagadas


def estatisticas() -> dict:
    entradas = list(CACHE_DIR.glob("*.json"))
    return {
        "entradas": len(entradas),
        "bytes": sum(p.stat().st_size for p in entradas),
    }
