"""Sugestão de classificação (catálogo 03). Nunca classifica de forma definitiva.

Tudo o que sai daqui é SUGESTÃO com confiança; a classificação só passa a valer
depois de decisão do APROVADOR. Entradas desconhecidas nunca viram receita (RN-03).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

PALAVRAS_DESPESA = [
    ("ACADEMIA", "02.03 saúde", "MEDIA"),
    ("FARMACIA", "02.03 saúde", "MEDIA"),
    ("DROGARIA", "02.03 saúde", "MEDIA"),
    ("SUPERMERC", "02.02 alimentação", "MEDIA"),
    ("MERCADO", "02.02 alimentação", "BAIXA"),
    ("RESTAUR", "02.02 alimentação", "BAIXA"),
    ("ESCOLA", "02.04 educação", "BAIXA"),
    ("COLEGIO", "02.04 educação", "BAIXA"),
    ("EMPRESA MUNIC", "02.05 transporte", "BAIXA"),
    ("ESTACIONA", "02.05 transporte", "MEDIA"),
    ("COMBUST", "02.05 transporte", "MEDIA"),
    ("TICKET", "02.07 lazer", "BAIXA"),
    ("CINEMA", "02.07 lazer", "MEDIA"),
    ("SEGURO", "02.10 seguros", "BAIXA"),
    ("IPTU", "02.11 tributos", "MEDIA"),
    ("NETFLIX", "02.13 tecnologia", "MEDIA"),
    ("SPOTIFY", "02.13 tecnologia", "MEDIA"),
    ("LOJA MACONICA", "02.08 pessoais", "BAIXA"),
]


def normalizar(t: str | None) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


@dataclass
class Sugestao:
    evento: str | None
    classe: str | None
    descricao: str
    confianca: str
    tarefa: str
    justificativa: str
    elegivel_lote: bool = False
    exige_individual: bool = False


@dataclass
class Contexto:
    titular: str | None          # nome do titular no extrato
    familia: dict                # PRIMEIRO_NOME -> papel (conjuge, filha, ...)
    empresas: dict = None        # ALIAS normalizado -> nome da empresa relacionada


def sugerir(historico: str, contraparte: str, valor, ctx: Contexto) -> Sugestao:
    h, cp = normalizar(historico), normalizar(contraparte)
    alvo = f"{h} {cp}"
    credito = valor > 0
    v = f"{valor:,.2f}"

    if "FAT" in h and "CART" in h:
        return Sugestao("PAGAMENTO_FATURA", None, "PAGAMENTO_FATURA (sem despesa; compras vêm da fatura)", "ALTA",
                        "CONFIRMAR_PAGAMENTO_FATURA",
                        f"Pagamento de fatura {v}: liquida passivo do cartão, não é despesa. As despesas entram "
                        "somente pela fatura detalhada, para não duplicar principal.", elegivel_lote=True)

    if ctx.titular:
        tit = normalizar(ctx.titular).split()
        if len(tit) >= 2 and cp.startswith(" ".join(tit[:2])):
            return Sugestao("TRANSFERENCIA_INTERNA", None, "TRANSFERENCIA_INTERNA provável (não é receita/despesa)",
                            "MEDIA", "CONFIRMAR_TRANSFERENCIA_INTERNA",
                            f"Movimento {v} com o próprio titular: informar conta de contrapartida. Se for conta "
                            "conjunta, a atribuição depende de IF09.")

    for chave, nome in (ctx.empresas or {}).items():
        if chave in alvo:
            return Sugestao(None, None, f"A_CLASSIFICAR; empresa relacionada provável: {nome}", "INDETERMINADA",
                            "CLASSIFICAR_EMPRESA_RELACIONADA",
                            f"{v} com {nome}: natureza não presumida (remuneração, lucros, aluguel, reembolso, "
                            "mútuo ou despesa de terceiro). Reembolso baixa crédito e não é receita (RN-03).")

    primeiro = cp.split()[0] if cp else ""
    if primeiro in ctx.familia:
        papel = ctx.familia[primeiro]
        if papel == "conjuge" and not credito and abs(valor) == 12000:
            return Sugestao("APORTE_DOMESTICO", None, "APORTE_DOMESTICO (transferência interna, F01)", "MEDIA",
                            "CONFIRMAR_APORTE_DOMESTICO",
                            "Aporte doméstico de 12.000: transferência interna, não despesa adicional da família.")
        return Sugestao(None, None, "A_CLASSIFICAR; nome coincide com membro da família (não presumir)",
                        "INDETERMINADA", "CLASSIFICAR_MOVIMENTO_FAMILIA",
                        f"{v}: nome coincide com membro da família. Classificar só se o aprovador quiser; "
                        "nenhum dado pessoal é solicitado.")

    if credito:
        return Sugestao(None, None, "A_CLASSIFICAR entrada (não presumir receita)", "INDETERMINADA",
                        "CLASSIFICAR_ENTRADA",
                        f"Entrada {v}: receita, reembolso, transferência ou outra natureza? Nada é reconhecido "
                        "como receita sem decisão.")

    for chave, classe, conf in PALAVRAS_DESPESA:
        if chave in alvo:
            return Sugestao(None, classe, f"{classe} (sugestão)", conf, "CONFIRMAR_CATEGORIA",
                            f"Saída {v}: categoria sugerida {classe}, confiança {conf}. Catálogo não homologado.",
                            elegivel_lote=(conf == "MEDIA"))

    return Sugestao(None, None, "A_CLASSIFICAR saída", "INDETERMINADA", "CLASSIFICAR_SAIDA",
                    f"Saída {v}: categoria a definir pelo aprovador.")
