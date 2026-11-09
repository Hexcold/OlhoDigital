"""Contratos de entrada e saída.

Vale mais do que validação: o JSON de saída do sistema é **entregável do TCC**
(objetivo específico i), e com Pydantic ele sai documentado no OpenAPI de graça,
em vez de existir só como exemplo colado no texto.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


# Categorias de horas complementares. Lista fechada de propósito: ela soma no
# relatório, e texto livre vira EXTENSAO/Extensão/extensao — três categorias
# onde deveria haver uma. Ampliar aqui é decisão de projeto, não digitação.
CATEGORIAS_EVENTO = ("ensino", "pesquisa", "extensao", "cultural", "esportiva",
                     "outra")
TIPOS_EVENTO = ("aula", "evento")


class EventoEntrada(BaseModel):
    """O cadastro de uma ocorrência (D15).

    A validação de coerência entre `tipo` e os campos específicos mora aqui, e
    não no banco, porque a mensagem de erro é para uma pessoa preenchendo um
    formulário — 'aula não tem categoria' resolve, `CHECK constraint violated`
    não.
    """
    chave: str
    tipo: Literal["aula", "evento"]
    nome: str
    responsavel: str | None = None
    local: str | None = None
    inicio: datetime | None = None
    fim: datetime | None = None

    disciplina: str | None = None
    turma: str | None = None
    semestre: str | None = None

    categoria: Literal[CATEGORIAS_EVENTO] | None = None  # type: ignore[valid-type]
    horas_complementares: float | None = None
    orgao_promotor: str | None = None

    @model_validator(mode="after")
    def _coerente(self) -> "EventoEntrada":
        if self.fim and self.inicio and self.fim <= self.inicio:
            raise ValueError("o fim tem que vir depois do início")
        proibidos = ({"categoria", "horas_complementares", "orgao_promotor"}
                     if self.tipo == "aula" else
                     {"disciplina", "turma", "semestre"})
        sobrando = sorted(c for c in proibidos if getattr(self, c) is not None)
        if sobrando:
            raise ValueError(f"tipo '{self.tipo}' não tem "
                             f"{', '.join(sobrando)}")
        return self


class EventoSaida(EventoEntrada):
    model_config = ConfigDict(from_attributes=True)
    id: int
    situacao: str
    criado_em: datetime
    # Quantas folhas já apontam para esta chave — é o que diz se dá para
    # encerrar ou apagar sem deixar folha órfã.
    #
    # NÃO se chama `folhas`: esse nome é o relacionamento em `tabelas.Evento`, e
    # com `from_attributes` o Pydantic leria a lista do ORM no lugar da
    # contagem. Colidiu de verdade ao escrever isto.
    folhas_enviadas: int = 0


class FolhaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    evento_id: int | None
    # a chave como foi lida e como o vínculo foi feito; ver `tabelas.Folha`
    chave_lida: str | None = None
    origem_vinculo: str | None = None
    evento_chave: str | None = None
    nome_original: str
    # um PDF de escâner traz várias folhas; a página diz qual delas é esta
    pagina: int
    modelo_folha: str
    estado: str
    erro: str | None
    linhas_preenchidas: int
    linhas_anuladas: int
    # o ponto de operação com que ESTA folha foi lida; ver tabelas.Folha
    alpha: float | None
    theta: float | None
    delta: float | None
    metrica_nome: str | None
    modelo_matricula: str | None
    modelo_letras: str | None
    criado_em: datetime
    processada_em: datetime | None
    # carimbo de revisão concluída — ver `tabelas.Folha`
    revisada_em: datetime | None = None
    revisada_por: str | None = None

    # Derivados. `situacao` é a etiqueta única que a tela mostra, combinando o
    # eixo do processamento com o da revisão; os dois contadores vêm junto
    # porque a lista precisaria de uma consulta por folha para obtê-los.
    situacao: str = "recebida"
    pendentes: int = 0
    duplicadas: int = 0


class Candidato(BaseModel):
    matricula: str
    nome: str
    curso: str | None = None
    score: float


def motivo_rejeicao(score: float, margem: float, theta: float,
                    delta: float) -> str:
    """Por que a linha caiu. As duas causas pedem atenção diferente.

    θ e δ caçam espécies diferentes de erro (D03): θ é o impostor confiante, δ
    é o vencedor ambíguo. Para o revisor a diferença é prática — score baixo
    significa "nenhum candidato se parece"; margem baixa significa "dois se
    parecem igualmente, olhe o nome". Portado do `quarentena.py`, que é a
    especificação desta tela.
    """
    baixo, apertado = score < theta, margem < delta
    if baixo and apertado:
        return "score baixo e margem apertada"
    if baixo:
        return f"score {score:.3f} abaixo de θ={theta:g}"
    if apertado:
        return f"margem {margem:.3f} abaixo de δ={delta:g}"
    return ""


def montar_linha(linha, vizinhos: list[str], theta: float,
                 delta: float) -> dict:
    """`Linha` do banco -> o que a tela de revisão precisa.

    `vizinhos` e `motivo_rejeicao` são DERIVADOS, não colunas: o primeiro
    depende da base vigente (que pode mudar sem a leitura mudar) e o segundo
    depende de θ e δ. Guardá-los congelaria no banco duas coisas que são
    função de parâmetros — o mesmo erro de gravar decisão sem gravar o limiar.

    A revisão vem junto, e a ÚLTIMA: a tabela é append-only, então revisar de
    novo empilha registros e o que vale é o topo. Sem isto, recarregar a página
    faria a revisão inteira parecer não feita.
    """
    dados = {c.name: getattr(linha, c.name)
             for c in linha.__table__.columns}
    dados["vizinhos"] = vizinhos
    dados["motivo_rejeicao"] = ("" if linha.decisao == "aceito"
                                else motivo_rejeicao(linha.score, linha.margem,
                                                     theta, delta))
    revisoes = sorted(linha.revisoes, key=lambda r: r.criado_em)
    dados["revisao"] = revisoes[-1] if revisoes else None
    return dados


def montar_folha(folha, pendentes: int = 0, duplicadas: int = 0) -> dict:
    """`Folha` do banco -> o que a lista precisa, com a etiqueta já resolvida."""
    dados = {c.name: getattr(folha, c.name) for c in folha.__table__.columns}
    dados["pendentes"] = pendentes
    dados["duplicadas"] = duplicadas
    dados["situacao"] = situacao_da_folha(folha, pendentes)
    dados["evento_chave"] = folha.evento.chave if folha.evento else None
    return dados


class VinculoEntrada(BaseModel):
    """A decisão humana sobre o evento da folha. Nulo tira o vínculo."""
    model_config = ConfigDict(extra="forbid")
    evento_id: int | None


class EventoSugerido(BaseModel):
    id: int
    chave: str
    nome: str
    distancia: int


class SugestaoEventos(BaseModel):
    """Por que a folha não foi ligada sozinha, e os eventos do mais provável ao
    menos provável."""
    motivo: str
    candidatos: list[EventoSugerido]


class ResumoRevisao(BaseModel):
    """O que a folha virou depois da revisão humana."""
    linhas: int
    identificados: int
    nao_identificados: int
    anuladas: int
    # Quarentena sem decisão. É o que impede concluir: aceite sem auditar é
    # escolha de método (amostragem), quarentena sem decidir é trabalho parado.
    pendentes: int
    duplicadas: int


def situacao_da_folha(folha, pendentes: int) -> str:
    """Uma etiqueta só, combinando os DOIS eixos que a folha tem.

    `estado` é o eixo do processamento (na fila → lendo → processada) e a
    revisão é outro eixo inteiro. Guardar os dois numa coluna só seria a mesma
    confusão que juntar leitura e limiar no mesmo campo: assim que aparecesse
    um estado novo em um dos eixos, ele teria que ser multiplicado pelo outro.

    A ordem importa — o eixo do processamento manda, porque não faz sentido
    falar de revisão de uma folha que ainda não foi lida.
    """
    if folha.estado in ("recebida", "processando", "erro", "suspeita",
                        "nao_e_folha"):
        return folha.estado
    # Folha em branco não é erro nem pendência: é uma folha que ninguém
    # preencheu. Aparecia como "pronta" com 0 linhas, o que engana.
    if folha.linhas_preenchidas == 0:
        return "em_branco"
    if folha.revisada_em is not None:
        return "revisada"
    if pendentes > 0:
        return "aguardando_revisao"
    # Lida, sem quarentena em aberto e sem carimbo: não precisa de ninguém,
    # mas também não foi conferida por ninguém. Os dois fatos importam.
    return "pronta"


class RevisaoEntrada(BaseModel):
    # nulo é decisão legítima: "não identificado". Não confundir com ausência
    # de revisão, que é a linha não ter registro nenhum.
    matricula_escolhida: str | None = None
    revisor: str | None = None
    # quanto tempo o revisor levou. A39 mediu 3,7 s por auditoria de aceite e
    # 15,8 s por triagem em n muito pequeno; gravar isto em produção transforma
    # aquela medição fraca em série contínua.
    ms: int | None = None


class RevisaoSaida(RevisaoEntrada):
    model_config = ConfigDict(from_attributes=True)
    id: int
    linha_id: int
    criado_em: datetime


class LinhaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folha_id: int
    numero: int
    leitura_matricula: str
    leitura_nome: str
    confiancas_matricula: list[float]
    confiancas_nome: list[float]
    motivos_matricula: list[str]
    motivos_nome: list[str]
    score: float
    margem: float
    decisao: str
    matricula_sugerida: str | None
    candidatos: list[Candidato]
    duplicada_na_folha: bool

    # derivados, não colunas — ver `montar_linha`
    vizinhos: list[str] = []
    motivo_rejeicao: str = ""
    # a última decisão humana sobre esta linha, se houver
    revisao: RevisaoSaida | None = None


class ParticipanteSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    matricula: str
    nome: str
    curso: str | None


class CabecalhoFolha(BaseModel):
    """Os textos editáveis da folha (D16).

    `extra="forbid"` é a trava na porta: o contrato só conhece estes campos, então
    um pedido que tente mexer em marcador ou grade recebe 422 em vez de ser
    ignorado em silêncio.
    """
    model_config = ConfigDict(extra="forbid")
    titulo: str
    subtitulo: str = ""
    complemento: str = ""
    instrucao: str = ""


class PedidoPrevia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cabecalho: CabecalhoFolha
    chave: str = ""


class PedidoFolhas(BaseModel):
    """Folhas avulsas, sem planilha. Chave vazia é a folha rotulada à mão."""
    model_config = ConfigDict(extra="forbid")
    chave: str = ""
    folhas: int = 1


class CabecalhoSaida(BaseModel):
    cabecalho: CabecalhoFolha
    limite_caracteres: int
    # O que a tela mostra como fora de alcance. Vem do servidor para ser a mesma
    # lista que o código garante.
    travado: list[str]


class ItemLote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linha: int
    chave: str
    folhas: int
    nome: str | None = None
    local: str | None = None
    responsavel: str | None = None
    dia: date | None = None     # o dia do evento, que vai no carimbo da folha


class LoteSaida(BaseModel):
    itens: list[ItemLote]
    erros: list[str]        # impedem gerar
    alertas: list[str]      # exigem confirmação
    total_chaves: int
    total_folhas: int


class PedidoPdf(BaseModel):
    itens: list[ItemLote]
    aceitar_alertas: bool = False


class PedidoCadastro(BaseModel):
    itens: list[ItemLote]


class CadastroSaida(BaseModel):
    criados: int
    ja_existiam: int


class LoginEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usuario: str
    senha: str


class SessaoSaida(BaseModel):
    usuario: str
    papel: str
    # falso no desenvolvimento local, onde não há porta de login
    login: bool
