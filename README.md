# DBC to CSV Converter

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

Ferramenta Python para converter arquivos DBC (banco de dados DATASUS) para CSV com máxima eficiência e simplicidade.

## 📋 Índice

- [Sobre](#sobre)
- [Características](#características)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Como Usar](#como-usar)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Como Funciona](#como-funciona)

## Sobre

Este projeto fornece uma solução simplificada e eficiente para converter arquivos DBC do [DATASUS](https://datasus.saude.gov.br/) para o formato CSV. Utiliza apenas dependências mínimas e bibliotecas da stdlib do Python, sem requerer pandas, pysus ou ferramentas externas pesadas.

**Ideal para:**
- Pesquisadores trabalhando com dados de saúde pública brasileiros
- Análise de dados epidemiológicos
- Migração de dados de bancos DATASUS para formatos abertos
- Automatização de pipelines de processamento de dados

## ✨ Características

- ✅ Conversão de DBC para CSV sem dependências pesadas
- ✅ Usa apenas `dbctodbf` (biblioteca pura Python) e stdlib
- ✅ Decodificação correta de campos numéricos compactados do DATASUS
- ✅ Processamento em lote de múltiplos arquivos
- ✅ Encoding automático (latin-1 → UTF-8)
- ✅ Relatório de progresso com contagem de sucessos/falhas
- ✅ Tratamento robusto de erros

## 🔧 Pré-requisitos

- Python 3.9 ou superior
- pip ou conda

## 📦 Instalação

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/datasus-dbc2csv-py.git
cd datasus-dbc2csv-py
```

### 2. Instale as dependências

```bash
pip install -r requirements.txt
```

Ou com conda:

```bash
conda create -n dbc2csv python=3.9
conda activate dbc2csv
pip install -r requirements.txt
```

## 🚀 Como Usar

### Uso Básico

1. Coloque seus arquivos `.dbc` no diretório `arquivos/dbc/`
2. Execute o script:

```bash
python dbc2csv.py
```

3. Os arquivos `.csv` serão gerados em `arquivos/csv/`

### Exemplo de Saída

```
OK   ABOBA2601.dbc -> ABOBA2601.csv
OK   ABOBA2602.dbc -> ABOBA2602.csv
OK   ABOBA2603.dbc -> ABOBA2603.csv
FALHA INVALID.dbc: Arquivo corrompido
...
Concluído: 47 convertido(s), 1 falha(s).
```

### Integração em Código Python

```python
from pathlib import Path
from dbc2csv import convert_dbc_to_csv

# Converter um arquivo específico
dbc_path = Path("arquivos/dbc/ABOBA2601.dbc")
csv_path = Path("arquivos/csv/ABOBA2601.csv")

convert_dbc_to_csv(dbc_path, csv_path)
print("Conversão concluída com sucesso!")
```

## 📁 Estrutura do Projeto

```
datasus-dbc2csv-py/
├── dbc2csv.py                  # Script principal de conversão
├── requirements.txt            # Dependências do projeto
├── README.md                   # Este arquivo
└── arquivos/
    ├── dbc/                    # 📥 Coloque seus arquivos .dbc aqui
    │   ├── ABOBA2601.dbc
    │   ├── ABOBA2602.dbc
    │   └── ...
    └── csv/                    # 📤 Arquivos convertidos são salvos aqui
        ├── ABOBA2601.csv
        ├── ABOBA2602.csv
        └── ...
```

## 🔍 Como Funciona

### Processo de Conversão

1. **Leitura do DBC**: O arquivo DBC é lido como bytes
2. **Descompressão**: Usa `DBCDecompress()` para extrair o DBF compactado
3. **Parsing do DBF**: Extrai metadados (campos, tipos, tamanhos)
4. **Decodificação de Campos**: 
   - Detecta campos numéricos compactados especiais do DATASUS
   - Converte de latin-1 para UTF-8
   - Remove espaçamento em branco
5. **Escrita CSV**: Salva os dados em formato CSV com UTF-8

### Tratamento de Campos Numéricos Compactados

O DATASUS usa uma codificação especial para campos numéricos longos (ex.: `AP_CNSPCN`), onde cada dígito é armazenado como `byte - 123` em vez de ASCII padrão. O script detecta e decodifica automaticamente:

```python
# Detecção automática
if raw and all(0x7B <= b <= 0x84 for b in raw):
    # Decodifica campo compactado
    resultado = "".join(str(b - 0x7B) for b in raw)
```

---

**Nota**: Este projeto é independente e não é afiliado ao DATASUS ou ao Ministério da Saúde.