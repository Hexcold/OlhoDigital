"""Contratos de entrada e saída.

Vale mais do que validação: o JSON de saída do sistema é **entregável do TCC**
(objetivo específico i), e com Pydantic ele sai documentado no OpenAPI de graça,
em vez de existir só como exemplo colado no texto.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def limpar_grupo(valor: str | None) -> str | None:
    """O nome de um grupo de eventos (D24), do jeito que fica guardado.

    Espaços nas pontas e repetidos saem, para "EU" e "EU " não virarem dois grupos; vazio é
    sem grupo. A caixa fica como foi digitada: o nome aparece na tela e na planilha.
    """
    if valor is None:
        return None
    limpo = " ".join(valor.split())
    if len(limpo) > 60:
        raise ValueError("o nome do grupo tem no máximo 60 caracteres")
    if "/" in limpo:
        raise ValueError("o nome do grupo não pode ter '/': ele vai no endereço da página")
    return limpo or None


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
    # D24. No PATCH só muda se vier: a edição manda o evento inteiro, e uma tela que ainda
    # não conhece o campo apagaria o grupo a cada edição
    grupo: str | None = None

    @field_validator("grupo")
    @classmethod
    def _grupo(cls, valor: str | None) -> str | None:
        return limpar_grupo(valor)

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
    encerrado_em: datetime | None = None
    # D35, fase 3
    cabecalho_ajuste: dict | None = None
    folhas_previstas: int | None = None
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
    # D23: a regra dos dois canais e o recorte da célula; nulos em folha lida antes deles
    tau_nome: float | None = None
    recorte: str | None = None
    corte_acaso: float | None = None                       # D37
    criado_em: datetime
    processada_em: datetime | None
    # carimbo de revisão concluída — ver `tabelas.Folha`
    revisada_em: datetime | None = None
    evento_grupo: str | None = None                        # D35
    grupo_envio: str | None = None                         # D35, fase 5
    # D30: outro escaneamento do mesmo papel; ver `api/reenvio.py`
    copia_de: int | None = None
    semelhanca_copia: float | None = None
    nao_e_copia: bool = False
    copia_de_rotulo: str | None = None
    # D29: o marcador cujo canto foi estimado (lida com 3); nulo com os 4
    marcador_estimado: int | None = None
    # A61: trocada por outro escaneamento do mesmo papel
    reescaneada_em: datetime | None = None
    arquivo_reescaneado: str | None = None
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
    # A66: com lista de grupo, se o candidato está nela; None sem lista
    na_lista: bool | None = None


# D26: quem grava o "não identificado" padrão da linha sem matrícula. É um nome de
# revisor e não uma coluna nova porque a decisão É uma revisão — a tela, o resumo e a
# frequência já sabem lê-la —, e o nome diz que ninguém olhou.
REVISOR_AUTOMATICO = "automático: sem matrícula"


def sem_matricula(motivos_matricula: list | None) -> bool:
    """Nenhum dígito lido na matrícula: só célula vazia ou resíduo de grade (`sem_caixa`).

    É a linha em que só o nome foi preenchido (D26). Com seis curingas a matrícula casa com
    a base inteira e o candidato sugerido é palpite do nome sozinho (A59), então a linha
    nasce "não identificado" e o revisor muda se reconhecer a pessoa. Matrícula PARCIAL não
    entra aqui: tem dígito lido, e segue o D22 como antes.
    """
    return bool(motivos_matricula) and all(m in ("vazia", "sem_caixa")
                                           for m in motivos_matricula)


def motivo_rejeicao(score: float, margem: float, theta: float,
                    delta: float, motivos_matricula: list | None = None,
                    tau_nome: float | None = None, fora_da_lista: bool = False,
                    nome_nao_confirma: bool = False) -> str:
    """Por que a linha caiu. As causas pedem atenção diferente.

    θ e δ caçam espécies diferentes de erro (D03): θ é o impostor confiante, δ
    é o vencedor ambíguo. Para o revisor a diferença é prática — score baixo
    significa "nenhum candidato se parece"; margem baixa significa "dois se
    parecem igualmente, olhe o nome". Portado do `quarentena.py`, que é a
    especificação desta tela.

    A matrícula com célula em branco vem primeiro, porque ela barra a linha
    mesmo com score e margem altos (D22): é a linha decidida só pelo nome, ou a
    linha vazia com resíduo de grade (A59).

    `tau_nome` é o da folha: se ela foi lida com a regra dos dois canais (D23) e
    nada acima explica a quarentena, foi a regra. Folha lida antes dela tem τ nulo
    e não ganha um motivo que não existia quando foi decidida.
    """
    if sem_matricula(motivos_matricula):
        return "sem matrícula: só o nome foi preenchido"
    vazias = sum(1 for m in (motivos_matricula or []) if m == "vazia")
    if vazias:
        return (f"matrícula incompleta: {vazias} de {len(motivos_matricula)} "
                "células em branco")
    if fora_da_lista:
        # o gate passou; quem barrou foi a lista (A66)
        return "a pessoa reconhecida não está na lista do grupo"
    baixo, apertado = score < theta, margem < delta
    if baixo and apertado:
        return "score baixo e margem apertada"
    if baixo:
        return f"score {score:.3f} abaixo de θ={theta:g}"
    if apertado:
        return f"margem {margem:.3f} abaixo de δ={delta:g}"
    if nome_nao_confirma:
        # D37: antes do τ, que é o motivo que sobra quando nada mais explica
        return "o nome lido não confirma a pessoa sugerida: casa igual ou melhor com muita gente da base"
    if tau_nome is not None:
        return f"matrícula não é exata e o nome não a confirma (dois canais, τ={tau_nome:g})"
    return ""


def ultima_revisao(linha):
    """A revisão que vale: a mais recente. O id desempata, porque `now()` do Postgres é a
    hora do início da transação, e duas revisões na mesma transação teriam a mesma hora.
    Uma função só para a tela, o resumo, o JSON e a frequência do grupo não divergirem."""
    return max(linha.revisoes, key=lambda r: (r.criado_em, r.id), default=None)


def montar_linha(linha, vizinhos: list[str], theta: float,
                 delta: float, tau_nome: float | None = None) -> dict:
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
                                                     theta, delta,
                                                     linha.motivos_matricula,
                                                     tau_nome,
                                                     bool(getattr(linha, "fora_da_lista",
                                                                  False)),
                                                     bool(getattr(linha, "nome_nao_confirma",
                                                                  False))))
    dados["revisao"] = ultima_revisao(linha)
    return dados


def montar_folha(folha, pendentes: int = 0, duplicadas: int = 0) -> dict:
    """`Folha` do banco -> o que a lista precisa, com a etiqueta já resolvida."""
    dados = {c.name: getattr(folha, c.name) for c in folha.__table__.columns}
    dados["pendentes"] = pendentes
    dados["duplicadas"] = duplicadas
    dados["situacao"] = situacao_da_folha(folha, pendentes)
    dados["evento_chave"] = folha.evento.chave if folha.evento else None
    dados["evento_grupo"] = folha.evento.grupo if folha.evento else None      # D35
    dados["copia_de_rotulo"] = None
    if getattr(folha, "copia_de", None) is not None:
        from sqlalchemy.orm import object_session
        sessao = object_session(folha)
        outra = sessao.get(type(folha), folha.copia_de) if sessao else None
        dados["copia_de_rotulo"] = rotulo_folha(outra) if outra else None
    return dados


def rotulo_folha(folha) -> str:
    """Como a tela nomeia uma folha: arquivo e página."""
    return f"{folha.nome_original}, página {folha.pagina}"


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
    # D33: a chave do primeiro candidato já foi de um evento encerrado. A folha pode ser
    # uma atrasada daquele evento, e por isso não foi ligada sozinha.
    aviso: str | None = None


class ResumoRevisao(BaseModel):
    """O que a folha virou depois da revisão humana."""
    linhas: int
    identificados: int
    nao_identificados: int
    # D32: presentes que não estão na base
    fora_da_base: int = 0
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
    # D30: outro escaneamento de uma folha que já conta. Não pede revisão nem conta presença.
    if getattr(folha, "copia_de", None) is not None:
        return "copia"
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
    # D32: "não está na base" — a matrícula e o nome conferidos por quem revisa. Sem matrícula,
    # a revisão vira "não identificado".
    matricula_informada: str | None = None
    nome_informado: str | None = None
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
    fora_da_lista: bool = False
    nome_nao_confirma: bool = False

    # derivados, não colunas — ver `montar_linha`
    vizinhos: list[str] = []
    motivo_rejeicao: str = ""
    # a última decisão humana sobre esta linha, se houver
    revisao: RevisaoSaida | None = None


class LinhaDoGrupo(LinhaSaida):
    """Uma linha da quarentena do grupo (D24): a da revisão, mais de onde ela veio."""
    evento_chave: str
    evento_nome: str
    folha_nome: str
    folha_pagina: int
    # folha concluída: a linha vira leitura, como na revisão de uma folha
    folha_concluida: bool


class ParticipanteSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    matricula: str
    nome: str
    curso: str | None
    na_lista: bool | None = None          # A66: com lista de grupo, se a pessoa está nela


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


class CabecalhoAjuste(BaseModel):
    """O que um evento troca no cabeçalho do grupo (D35, fase 3). Ausente fica como no grupo."""
    model_config = ConfigDict(extra="forbid")
    titulo: str | None = None
    subtitulo: str | None = None
    complemento: str | None = None
    instrucao: str | None = None


class PedidoPrevia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cabecalho: CabecalhoFolha
    chave: str = ""


class PedidoFolhas(BaseModel):
    """Folhas avulsas, sem planilha. Chave vazia é a folha rotulada à mão."""
    model_config = ConfigDict(extra="forbid")
    chave: str = ""
    folhas: int = 1
    # D35: o modelo do grupo; sem ele, o padrão salvo (o `cabecalho.json`)
    cabecalho: CabecalhoFolha | None = None


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
    # D35, fase 3: o modelo do grupo e, por chave, o que cada evento troca nele. Sem eles,
    # o padrão salvo. Vêm no pedido porque este módulo não lê banco (D19).
    cabecalho: CabecalhoFolha | None = None
    ajustes: dict[str, CabecalhoAjuste] | None = None


class PacoteGrupo(BaseModel):
    """O pacote de impressão de um grupo, para o PC de quem imprime (D19, D35 fase 3)."""
    model_config = ConfigDict(extra="forbid")
    formato: str
    versao: int
    grupo: str
    gerado_em: str | None = None
    cabecalho: CabecalhoFolha
    ajustes: dict[str, CabecalhoAjuste] = {}
    itens: list[ItemLote]


class PacoteSaida(BaseModel):
    grupo: str
    cabecalho: CabecalhoFolha
    ajustes: dict[str, CabecalhoAjuste]
    lote: "LoteSaida"


class PedidoCadastro(BaseModel):
    itens: list[ItemLote]
    # D24: o grupo dos eventos criados por este cadastro (os que já existiam não mudam)
    grupo: str | None = None

    @field_validator("grupo")
    @classmethod
    def _grupo(cls, valor: str | None) -> str | None:
        return limpar_grupo(valor)


class GrupoEntrada(BaseModel):
    """Criar ou renomear um grupo (D35)."""
    model_config = ConfigDict(extra="forbid")
    nome: str
    descricao: str | None = None

    @field_validator("nome")
    @classmethod
    def _nome(cls, valor: str) -> str:
        limpo = limpar_grupo(valor)
        if not limpo:
            raise ValueError("o grupo precisa de um nome")
        return limpo


class ModeloGrupoSaida(BaseModel):
    """O modelo da folha de um grupo (D35, fase 3)."""
    cabecalho: CabecalhoFolha            # o que vale: o do grupo ou, sem ele, o padrão
    proprio: bool                        # o grupo tem modelo próprio?
    limite_caracteres: int
    travado: list[str]
    # por chave, o que cada evento aberto troca no modelo
    ajustes: dict[str, CabecalhoAjuste]


class ModeloGrupoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cabecalho: CabecalhoFolha | None     # nulo volta ao padrão


class FilaSaida(BaseModel):
    """O progresso da leitura, para a tela mostrar sem perguntar folha por folha."""
    na_fila: int
    lendo: int
    # o lote: as folhas que entraram na fila desde que a mais antiga das pendentes entrou
    lote_total: int
    lote_prontas: int
    lendo_agora: str | None = None
    # o ritmo das últimas leituras (mediana entre leituras seguidas), ou 9 s sem histórico
    segundos_por_folha: float
    estimativa_s: float


class GrupoSaida(BaseModel):
    """Um grupo de eventos em uso, com o que ele junta."""
    nome: str
    eventos: int
    folhas: int
    folhas_revisadas: int
    # D33: quantos eventos do grupo ainda estão abertos
    eventos_abertos: int = 0
    # D35: a etapa do grupo no fluxo — preparando, recebendo, revisando, pronto, encerrado
    etapa: str = "preparando"
    descricao: str | None = None
    # quarentena sem decisão nas folhas que contam
    pendentes: int = 0
    # D30: das folhas, quantas são outro escaneamento de um papel que já conta (não contam),
    # quantos papéis foram escaneados uma vez só, e quantos têm cópia guardada
    copias: int = 0
    leitura_unica: int = 0
    # folhas que contam e ainda não tiveram a revisão concluída
    a_concluir: int = 0
    com_copia: int = 0


class PedidoGrupo(BaseModel):
    """Pôr vários eventos num grupo de uma vez; `grupo` nulo ou vazio tira do grupo."""
    evento_ids: list[int] = Field(min_length=1)
    grupo: str | None = None

    @field_validator("grupo")
    @classmethod
    def _grupo(cls, valor: str | None) -> str | None:
        return limpar_grupo(valor)


class PreviaApagar(BaseModel):
    """O que um apagar em massa vai fazer, antes de fazer.

    `revisadas_mantidas`: folhas concluídas, que carregam a frequência apurada e só saem com
    `incluir_revisadas`. `na_fila_mantidas`: folhas ainda sendo lidas, que nunca saem no meio
    da leitura.
    """
    apagar: int
    revisadas_mantidas: int
    na_fila_mantidas: int


class PedidoApagarFolhas(BaseModel):
    """`confirmacao` é o número de folhas que a pessoa viu na prévia e digitou: se a conta do
    servidor der outro número, nada é apagado."""
    confirmacao: int
    incluir_revisadas: bool = False


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
