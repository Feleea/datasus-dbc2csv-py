"""Metadados sobre os sistemas DATASUS presentes em arquivos/csv.

Cada grupo (prefixo de arquivo) corresponde a um sistema diferente do
DATASUS, cada um com seu próprio layout de colunas. Este módulo guarda o
essencial pra cruzar dados entre eles: qual coluna representa o CNES
(estabelecimento) e qual representa o procedimento em cada sistema.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Sistema:
    prefixo: str
    nome: str
    coluna_cnes: str | None
    coluna_procedimento: str | None


SISTEMAS: list[Sistema] = [
    Sistema("ABOBA", "APAC - Acompanhamento Pós Cirurgia Bariátrica", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("ACFBA", "APAC - Confecção de Fístula", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("ADBA", "APAC - Laudos Diversos", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("AMBA", "APAC - Acompanhamento Multiprofissional", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("AQBA", "APAC - Quimioterapia", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("ARBA", "APAC - Radioterapia", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("ATDBA", "APAC - Tratamento Dialítico", "AP_CODUNI", "AP_PRIPAL"),
    Sistema("ERBA", "SIA - Consistência/Erros", "CNES", None),
    Sistema("PABA", "SIA - Produção Ambulatorial (BPA/PA)", "PA_CODUNI", "PA_PROC_ID"),
    Sistema("PSBA", "SIA - Boletim de Produção Ambulatorial Individualizado (PSBA)", "CNES_EXEC", "PA_PROC_ID"),
    Sistema("RDBA", "SIH - Reduzida de AIH (internações)", "CNES", "PROC_REA"),
    Sistema("SPBA", "SIH - Serviços Profissionais (SP)", "SP_CNES", "SP_PROCREA"),
]

SISTEMAS_POR_PREFIXO: dict[str, Sistema] = {s.prefixo: s for s in SISTEMAS}
