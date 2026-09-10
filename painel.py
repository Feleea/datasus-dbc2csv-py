"""Painel web (Streamlit) para explorar os arquivos DATASUS convertidos.

Duas visões:
- Por arquivo: schema detalhado (coluna, nome real, % preenchido, valores
  distintos, exemplo) e os dados em si, paginados.
- Visão geral: cruza CNES e procedimento entre todos os sistemas, mostrando
  quais códigos se repetem e entre quantos/quais sistemas.

Rodar com: streamlit run painel.py
"""
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

import cache
import dados
from sistemas import SISTEMAS, SISTEMAS_POR_PREFIXO

st.set_page_config(page_title="Painel DATASUS", layout="wide")

PAGE_SIZE = 200

def mostrar_grid(
    df: pd.DataFrame,
    altura: int = 420,
    dicas: dict[str, str] | None = None,
    colunas_subtotal: list[str] | None = None,
) -> None:
    """Mostra `df` num grid com filtro e ordenação por coluna (clique no
    cabeçalho), igual planilha. `dicas` é um dict coluna -> texto de tooltip
    (nome real do campo). Se `colunas_subtotal` for passado, essas colunas
    numéricas ganham uma linha fixa no topo com a soma das linhas visíveis
    (respeitando os filtros aplicados, recalculada no navegador)."""
    if df.empty:
        st.info("Sem dados para exibir.")
        return
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(
        filter=True,
        sortable=True,
        resizable=True,
        floatingFilter=True,
        wrapHeaderText=True,
        autoHeaderHeight=True,
        # Permite até 3 condições no filtro de coluna (ex.: "A" OR "B" OR "C"),
        # em vez do padrão de 2. Recurso da edição Community do AG Grid.
        filterParams={"maxNumConditions": 3},
    )
    for col in df.columns:
        # minWidth garante o título visível mesmo se o autosize abaixo errar
        # a largura de alguma coluna (ele mede o conteúdo, não sempre o
        # cabeçalho); wrapHeaderText permite o título quebrar em duas linhas
        # em vez de ser cortado com "...".
        gb.configure_column(col, minWidth=max(90, len(str(col)) * 8 + 40))
    for col, texto in (dicas or {}).items():
        if col in df.columns and texto:
            gb.configure_column(col, headerTooltip=texto)
    # from_dataframe já configura autoSizeStrategy="fitGridWidth", que espreme
    # todas as colunas para caber na largura do grid (cortando os títulos).
    # Sobrescrevemos para "fitCellContents", que dá a cada coluna a largura
    # necessária para mostrar cabeçalho e conteúdo por completo.
    gb.configure_grid_options(autoSizeStrategy={"type": "fitCellContents"})
    grid_options = gb.build()

    if colunas_subtotal:
        coluna_rotulo = next((c for c in df.columns if c not in colunas_subtotal), df.columns[0])
        linha_subtotal = {c: "" for c in df.columns}
        linha_subtotal[coluna_rotulo] = "Subtotal (filtrado)"
        for c in colunas_subtotal:
            linha_subtotal[c] = int(df[c].sum())
        grid_options["pinnedTopRowData"] = [linha_subtotal]
        # Recalcula a linha fixa no navegador (sem round-trip ao Streamlit)
        # somando só as linhas que sobrevivem ao filtro atual.
        grid_options["onFilterChanged"] = JsCode(
            f"""
            function(params) {{
                const colunasSubtotal = {colunas_subtotal!r};
                const colunaRotulo = {coluna_rotulo!r};
                const soma = {{}};
                colunasSubtotal.forEach(c => soma[c] = 0);
                params.api.forEachNodeAfterFilter(node => {{
                    if (!node.data) return;
                    colunasSubtotal.forEach(c => {{
                        const v = node.data[c];
                        soma[c] += (typeof v === "number" ? v : 0);
                    }});
                }});
                const linha = {{}};
                linha[colunaRotulo] = "Subtotal (filtrado)";
                colunasSubtotal.forEach(c => linha[c] = soma[c]);
                params.api.setGridOption("pinnedTopRowData", [linha]);
            }}
            """
        )

    AgGrid(
        df,
        gridOptions=grid_options,
        height=altura,
        theme="streamlit",
        allow_unsafe_jscode=bool(colunas_subtotal),
    )


@st.cache_resource
def get_conexao():
    return dados.conectar()


@st.cache_data
def get_dicionario():
    return dados.carregar_dicionario()


@st.cache_data
def get_grupos():
    return dados.listar_arquivos()


@st.cache_resource
def _faxina_inicial():
    """Descarta entradas de cache antigas uma vez por processo."""
    return cache.descartar_antigos()


_faxina_inicial()


# As buscas caras passam por duas camadas de cache: o @st.cache_data guarda
# o resultado na memória enquanto o painel está de pé, e o cache.memoizar
# guarda em disco, para a busca continuar instantânea depois de reiniciar.
# A paginação dos dados (get_amostra) fica só na memória de propósito: são
# consultas baratas e gravar cada página visitada encheria o disco à toa.


@st.cache_data(show_spinner="Calculando estatísticas do arquivo (primeira vez pode demorar)...")
def get_schema(grupo: str, arquivo: str):
    paths = dados.paths_do_grupo(grupo, arquivo)
    resultado = cache.memoizar(
        "schema",
        {"grupo": grupo, "arquivo": arquivo},
        dados.fingerprint(paths),
        lambda: dados.schema_e_estatisticas(get_conexao(), paths, get_dicionario()),
    )
    # Vindo do cache em disco a tupla volta como lista (JSON não tem tupla);
    # normaliza para o retorno ser o mesmo nos dois caminhos.
    total, schema = resultado
    return total, schema


@st.cache_data
def get_amostra(grupo: str, arquivo: str, pagina: int):
    paths = dados.paths_do_grupo(grupo, arquivo)
    return dados.amostra(get_conexao(), paths, PAGE_SIZE, pagina * PAGE_SIZE)


@st.cache_data(show_spinner="Cruzando dados entre todos os sistemas (pode levar mais de um minuto)...")
def get_cruzamento(campo: str):
    paths = dados.paths_cruzamento(campo)
    return cache.memoizar(
        "cruzamento",
        {"campo": campo},
        dados.fingerprint(paths),
        lambda: dados.cruzamento(get_conexao(), campo),
    )


@st.cache_data(show_spinner="Buscando o CNES em todos os sistemas (pode levar dezenas de segundos)...")
def get_detalhe_cnes(cnes: str):
    paths = dados.paths_detalhe_cnes()
    return cache.memoizar(
        "detalhe_cnes",
        {"cnes": cnes},
        dados.fingerprint(paths),
        lambda: dados.detalhe_cnes(get_conexao(), cnes),
    )


dicionario = get_dicionario()
grupos = get_grupos()


def _formatar_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


with st.sidebar:
    st.header("Desempenho")

    status = dados.status_parquet()
    if status["total"] == 0:
        st.info("Nenhum CSV encontrado em `arquivos/csv`.")
    elif status["convertidos"] == status["total"]:
        st.success(
            f"Camada Parquet completa: {status['convertidos']} arquivo(s), "
            f"{_formatar_bytes(status['bytes_parquet'])} "
            f"(contra {_formatar_bytes(status['bytes_csv'])} em CSV)."
        )
    else:
        st.warning(
            f"{status['convertidos']} de {status['total']} arquivo(s) convertidos para Parquet. "
            "Os demais são lidos do CSV — correto, porém bem mais lento."
        )
        st.caption("Para acelerar, rode no terminal:")
        st.code("python csv2parquet.py", language="bash")

    st.header("Cache de buscas")
    info = cache.estatisticas()
    st.caption(
        f"{info['entradas']} busca(s) salva(s) em disco — {_formatar_bytes(info['bytes'])}. "
        "Buscas repetidas voltam instantâneas, mesmo depois de reiniciar o painel."
    )
    if st.button("Limpar cache de buscas", width="stretch"):
        apagadas = cache.limpar()
        st.cache_data.clear()
        st.success(f"{apagadas} busca(s) apagada(s).")
    st.caption(
        "O cache se invalida sozinho quando um CSV é adicionado, alterado ou "
        f"removido. Entradas com mais de {cache.VALIDADE_DIAS} dias são descartadas na abertura."
    )


st.title("Painel DATASUS")

aba_arquivo, aba_geral, aba_cnes = st.tabs(
    ["📄 Por arquivo", "🔗 Visão geral (cruzamento entre sistemas)", "🏥 Detalhe por CNES"]
)

# ---------------------------------------------------------------- por arquivo
with aba_arquivo:
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        prefixo = st.selectbox(
            "Sistema",
            options=sorted(grupos.keys()),
            format_func=lambda p: f"{p} — {SISTEMAS_POR_PREFIXO[p].nome}" if p in SISTEMAS_POR_PREFIXO else p,
        )
    arquivos_do_grupo = [p.name for p in grupos[prefixo]]
    with col_sel2:
        arquivo = st.selectbox(
            "Arquivo",
            options=[dados.TODOS_OS_ARQUIVOS] + arquivos_do_grupo,
            format_func=lambda a: (
                f"Todos ({len(arquivos_do_grupo)} arquivos, consolidado)"
                if a == dados.TODOS_OS_ARQUIVOS
                else a
            ),
        )

    total, schema = get_schema(prefixo, arquivo)
    st.caption(f"{total:,} registro(s) — {len(schema)} coluna(s)".replace(",", "."))

    st.subheader("Estrutura das colunas")
    schema_df = pd.DataFrame(schema).rename(
        columns={
            "coluna": "Coluna (código)",
            "nome_real": "Nome real",
            "exemplo": "Exemplo",
        }
    )
    mostrar_grid(schema_df)

    st.subheader("Dados")
    n_paginas = max(1, -(-total // PAGE_SIZE))
    pagina = st.number_input("Página", min_value=1, max_value=n_paginas, value=1, step=1) - 1
    linhas = get_amostra(prefixo, arquivo, pagina)
    df = pd.DataFrame(linhas)
    mostrar_grid(df, dicas=dicionario)
    st.caption(f"Página {pagina + 1} de {n_paginas}")

# ------------------------------------------------------------------ geral
with aba_geral:
    st.write(
        "Verifica se os mesmos códigos de **CNES** (estabelecimento) e de "
        "**procedimento** aparecem em mais de um sistema DATASUS."
    )

    campo_label = st.radio("Cruzar por", options=["CNES", "Procedimento"], horizontal=True)
    campo = "coluna_cnes" if campo_label == "CNES" else "coluna_procedimento"

    sistemas_com_campo = [s.prefixo for s in SISTEMAS if getattr(s, campo) is not None]
    st.caption(f"Sistemas considerados: {', '.join(sistemas_com_campo)}")

    st.info(
        "Essa consulta varre todos os arquivos dos sistemas acima (pode levar mais de "
        "um minuto na primeira vez de cada campo; fica em cache depois)."
    )
    chave_estado = f"cruzamento_{campo}"
    if st.button(f"Calcular cruzamento de {campo_label}", key=f"botao_{campo}"):
        st.session_state[chave_estado] = get_cruzamento(campo)

    resultado = st.session_state.get(chave_estado)
    if resultado is None:
        st.caption("Clique no botão acima para calcular.")
    else:
        n_compartilhados = len(resultado["compartilhados"])
        c1, c2 = st.columns(2)
        c1.metric(f"{campo_label} distintos no total", f"{resultado['total_distintos']:,}".replace(",", "."))
        c2.metric(f"{campo_label} em mais de 1 sistema (top 500)", n_compartilhados)

        st.subheader(f"{campo_label}s repetidos entre sistemas")
        if resultado["compartilhados"]:
            tabela = pd.DataFrame(resultado["compartilhados"])
            tabela["sistemas"] = tabela["sistemas"].apply(lambda v: ", ".join(v))
            tabela = tabela.rename(
                columns={"valor": campo_label, "n_sistemas": "Nº de sistemas", "sistemas": "Sistemas"}
            )
            mostrar_grid(tabela)
        else:
            st.info(f"Nenhum {campo_label} aparece em mais de um sistema.")

        st.subheader("Sobreposição entre pares de sistemas")
        if resultado["matriz"]:
            matriz_df = pd.DataFrame(resultado["matriz"])
            pivot = matriz_df.pivot(index="sistema_a", columns="sistema_b", values="comuns").fillna(0).astype(int)
            mostrar_grid(pivot.reset_index())
        else:
            st.info("Sem dados de sobreposição para exibir.")

# ------------------------------------------------------------------ detalhe por CNES
with aba_cnes:
    st.write(
        "Digite um código de CNES para ver, em cada sistema/arquivo onde ele aparece, "
        "quais procedimentos foram feitos, em qual competência e quantas vezes."
    )
    sistemas_com_ambos = [s.prefixo for s in SISTEMAS if s.coluna_cnes and s.coluna_procedimento]
    st.caption(
        f"Sistemas considerados (têm coluna de CNES e de procedimento): {', '.join(sistemas_com_ambos)}. "
        f"Digite o código exatamente como aparece nos dados (ex.: 2487756 ou 0003816) — "
        "veja exemplos na aba \"Visão geral\"."
    )

    col_cnes, col_botao = st.columns([3, 1])
    with col_cnes:
        cnes_digitado = st.text_input("CNES", label_visibility="collapsed", placeholder="ex.: 2487756")
    with col_botao:
        buscar = st.button("Buscar", width="stretch")

    if buscar and cnes_digitado.strip():
        st.session_state["detalhe_cnes_valor"] = cnes_digitado.strip()
        st.session_state["detalhe_cnes_resultado"] = get_detalhe_cnes(cnes_digitado.strip())

    cnes_valor = st.session_state.get("detalhe_cnes_valor")
    detalhe = st.session_state.get("detalhe_cnes_resultado")

    if not cnes_valor:
        st.caption("Nenhuma busca feita ainda.")
    elif not detalhe:
        st.warning(f"CNES \"{cnes_valor}\" não encontrado em nenhum dos sistemas acima.")
    else:
        detalhe_df = pd.DataFrame(detalhe)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ocorrências (total)", f"{int(detalhe_df['quantidade'].sum()):,}".replace(",", "."))
        c2.metric("Procedimentos distintos", detalhe_df["procedimento"].nunique())
        c3.metric("Competências", detalhe_df["competencia"].nunique())
        c4.metric("Arquivos", detalhe_df["arquivo"].nunique())

        st.subheader(f"Resumo por procedimento — CNES {cnes_valor}")
        resumo = (
            detalhe_df.groupby("procedimento")
            .agg(
                quantidade_total=("quantidade", "sum"),
                competencias=("competencia", lambda v: v.nunique()),
                arquivos=("arquivo", lambda v: v.nunique()),
                sistemas=("sistema", lambda v: ", ".join(sorted(set(v)))),
            )
            .reset_index()
            .sort_values("quantidade_total", ascending=False)
            .rename(
                columns={
                    "procedimento": "Procedimento",
                    "quantidade_total": "Quantidade (total)",
                    "competencias": "Nº competências",
                    "arquivos": "Nº arquivos",
                    "sistemas": "Sistemas",
                }
            )
        )
        mostrar_grid(resumo)

        st.subheader("Detalhe (procedimento × sistema, uma coluna por competência)")
        detalhado = (
            detalhe_df.pivot_table(
                index=["procedimento", "sistema"],
                columns="competencia",
                values="quantidade",
                aggfunc="sum",
                fill_value=0,
            )
            .astype(int)
            .reset_index()
            .rename(columns={"procedimento": "Procedimento", "sistema": "Sistema"})
        )
        colunas_competencia = sorted(
            (c for c in detalhado.columns if c not in ("Procedimento", "Sistema")), reverse=True
        )
        detalhado = detalhado[["Procedimento", "Sistema", *colunas_competencia]]
        mostrar_grid(detalhado, colunas_subtotal=colunas_competencia)
