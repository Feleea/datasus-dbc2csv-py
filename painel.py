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
from st_aggrid import AgGrid, GridOptionsBuilder

import dados
from sistemas import SISTEMAS, SISTEMAS_POR_PREFIXO

st.set_page_config(page_title="Painel DATASUS", layout="wide")

PAGE_SIZE = 200


def mostrar_grid(df: pd.DataFrame, altura: int = 420, dicas: dict[str, str] | None = None) -> None:
    """Mostra `df` num grid com filtro e ordenação por coluna (clique no
    cabeçalho), igual planilha. `dicas` é um dict coluna -> texto de tooltip
    (nome real do campo)."""
    if df.empty:
        st.info("Sem dados para exibir.")
        return
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(filter=True, sortable=True, resizable=True, floatingFilter=True)
    for col, texto in (dicas or {}).items():
        if col in df.columns and texto:
            gb.configure_column(col, headerTooltip=texto)
    AgGrid(df, gridOptions=gb.build(), height=altura, theme="streamlit")


@st.cache_resource
def get_conexao():
    return dados.conectar()


@st.cache_data
def get_dicionario():
    return dados.carregar_dicionario()


@st.cache_data
def get_grupos():
    return dados.listar_arquivos()


@st.cache_data(show_spinner="Calculando estatísticas do arquivo (primeira vez pode demorar)...")
def get_schema(grupo: str, arquivo: str):
    con = get_conexao()
    grupos = get_grupos()
    paths = grupos[grupo] if arquivo == "__todos__" else [p for p in grupos[grupo] if p.name == arquivo]
    return dados.schema_e_estatisticas(con, paths, get_dicionario())


@st.cache_data
def get_amostra(grupo: str, arquivo: str, pagina: int):
    con = get_conexao()
    grupos = get_grupos()
    paths = grupos[grupo] if arquivo == "__todos__" else [p for p in grupos[grupo] if p.name == arquivo]
    return dados.amostra(con, paths, PAGE_SIZE, pagina * PAGE_SIZE)


@st.cache_data(show_spinner="Cruzando dados entre todos os sistemas (pode levar mais de um minuto)...")
def get_cruzamento(campo: str):
    con = get_conexao()
    return dados.cruzamento(con, campo)


@st.cache_data(show_spinner="Buscando o CNES em todos os sistemas (pode levar dezenas de segundos)...")
def get_detalhe_cnes(cnes: str):
    con = get_conexao()
    return dados.detalhe_cnes(con, cnes)


dicionario = get_dicionario()
grupos = get_grupos()

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
            options=["__todos__"] + arquivos_do_grupo,
            format_func=lambda a: f"Todos ({len(arquivos_do_grupo)} arquivos, consolidado)" if a == "__todos__" else a,
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
        colunas_competencia = sorted(c for c in detalhado.columns if c not in ("Procedimento", "Sistema"))
        detalhado = detalhado[["Procedimento", "Sistema", *colunas_competencia]]
        mostrar_grid(detalhado)
