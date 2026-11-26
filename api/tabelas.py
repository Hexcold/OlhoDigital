"""Esquema do banco.

Chama-se `tabelas` e não `modelos` de propósito: neste projeto "modelo" já
significa duas outras coisas — o `ModeloFolha` da geometria de recorte e o
modelo Keras do reconhecedor. Um terceiro sentido na mesma base de código é
como se perdem tardes.

## O que este esquema decide, e por quê

**A imagem não entra no banco.** Fica em disco, e a tabela guarda o caminho e o
sha256. São dado pessoal sob LGPD, e arquivo é mais fácil de apagar.

**Cada folha registra o PONTO DE OPERAÇÃO com que foi lida.** α, θ, δ, a métrica
do nome e — principalmente — *qual modelo leu cada campo*. Sem isso, daqui a
dois meses ninguém sabe se uma folha foi lida com a CNN base ou com a afinada
(D12), e a diferença é de 6 pontos por célula. Constante de calibração que não
viaja junto do resultado é a origem de metade dos bugs registrados no vault.

**A linha guarda score e margem, não só a decisão.** É o *pontue uma vez,
limiarize muitas* do projeto virando esquema: mudar θ ou δ depois não pode
obrigar a reprocessar 500 folhas a 9 s cada.

**A revisão guarda o tempo gasto.** A39 mediu 3,7 s por auditoria de aceite e
15,8 s por triagem de quarentena em n=16 e n=1. Gravar `ms` em produção
transforma essa medição fraca em série contínua, de graça.
"""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB no Postgres, JSON em qualquer outro dialeto. A variante existe para o
# esquema continuar compilável fora do Postgres — é o que permite conferir as
# definições sem um servidor no ar.
Json = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Grupo(Base):
    """Um grupo de eventos: as ocorrências de uma mesma coleta (D35).

    Era só um texto no evento (D24), de propósito, para não haver cadastro de grupos. O D35
    pôs o grupo no centro do fluxo: ele é criado antes dos eventos, tem etapa e (na fase 3) o
    modelo da folha. O evento continua apontando para ele PELO NOME, com chave estrangeira
    `ON UPDATE CASCADE`: renomear o grupo renomeia em todos os eventos, e as rotas que já
    usavam o nome seguem valendo.
    """
    __tablename__ = "grupo"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D35, fase 3: o modelo da folha do grupo (os 4 textos editáveis de D16). Nulo é o
    # padrão salvo em `cabecalho.json`: grupo que nunca mexeu imprime como antes.
    cabecalho: Mapped[dict | None] = mapped_column(Json, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Evento(Base):
    """Uma OCORRÊNCIA de coleta — o que a chave da folha aponta (D15).

    Não é "os Encontros", que era o que esta tabela dizia antes de D15. A
    unidade é **a sala numa janela de tempo**: cinco apresentações das 8h às 10h
    na sala 2 do bloco 2 são um evento; a multiuso das 8h às 12h é outro. Um dia
    de Encontros com 10 salas × 4 períodos são 40 eventos, e uma turma com 30
    encontros são 30 eventos.

    Várias folhas dividem um evento — uma sala cheia não cabe em 33 linhas.

    **Não há coluna de recorrência.** Ela descreveria a turma, não a ocorrência,
    e aqui cada ocorrência é uma linha. A recorrência vive no gerador em lote,
    que expande "toda terça, 3M12" em N eventos. Para juntar as ocorrências de
    uma turma no relatório, o agrupamento é por (disciplina, turma, semestre).
    """
    __tablename__ = "evento"
    # D33: a chave é única só entre os abertos (índice parcial, no Postgres e no SQLite)
    __table_args__ = (Index("uq_evento_chave_aberta", "chave", unique=True,
                            postgresql_where=text("situacao = 'em andamento'"),
                            sqlite_where=text("situacao = 'em andamento'")),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # A junção impressa no papel. Única ENTRE OS EVENTOS ABERTOS (D33): duas ocorrências
    # abertas com a mesma chave são indistinguíveis para o sistema — a folha iria para as
    # duas. Encerrado, o evento guarda a chave dele, e ela fica livre para outro evento.
    # A–Z apenas (D14): é o que deixa a chave ser lida pelo classificador de 26
    # classes sem trazer de volta as confusões 0/O, 5/S, 2/Z e 1/I.
    chave: Mapped[str] = mapped_column(String(16), index=True)
    tipo: Mapped[str] = mapped_column(String(10))          # aula | evento
    nome: Mapped[str] = mapped_column(String(200))
    responsavel: Mapped[str | None] = mapped_column(String(200), nullable=True)
    local: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # A janela da ocorrência: 31/08 08:00–10:00. Serve de conferência cruzada —
    # folha atribuída a evento fora da janela é alerta, no espírito dos dois
    # sinais de A43.
    inicio: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fim: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Encerrar tira a chave do contradomínio contra o qual a leitura casa. É
    # D07 aplicado a outro conjunto: contradomínio pequeno é o que torna o gate
    # fácil, e chave de semestre passado só atrapalha.
    situacao: Mapped[str] = mapped_column(String(20), default="em andamento")
    # D35, fase 3: o que este evento troca no modelo do grupo (só campos de D16), e quantas
    # folhas a planilha pediu para ele, que o pacote de impressão precisa saber
    cabecalho_ajuste: Mapped[dict | None] = mapped_column(Json, nullable=True)
    folhas_previstas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # D33: quando foi encerrado. Entra no aviso da chave reaproveitada.
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- só quando tipo == 'aula'
    disciplina: Mapped[str | None] = mapped_column(String(200), nullable=True)
    turma: Mapped[str | None] = mapped_column(String(20), nullable=True)
    semestre: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # --- só quando tipo == 'evento'
    # Categoria é lista controlada, não texto livre: ela alimenta horas
    # complementares, e EXTENSAO/Extensão/extensao fazem o relatório não somar.
    categoria: Mapped[str | None] = mapped_column(String(50), nullable=True)
    horas_complementares: Mapped[float | None] = mapped_column(Float, nullable=True)
    orgao_promotor: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # O grupo de eventos: as ocorrências de uma mesma edição de algo maior, como todos os
    # eventos dos Encontros Universitários no grupo "EU". Texto livre e opcional; a lista de
    # grupos é a dos nomes em uso. É por ele que a frequência se baixa de uma vez e que as
    # folhas se apagam juntas (D24). Não confundir com o agrupamento das FOLHAS, que é pela
    # chave (D15): o grupo junta eventos, não folhas.
    # D35: todo evento pertence a um grupo (garantido pela API; a coluna segue anulável para
    # as fábricas dos testes antigos). A chave estrangeira é pelo nome, com cascata.
    grupo: Mapped[str | None] = mapped_column(
        String(60), ForeignKey("grupo.nome", onupdate="CASCADE"), nullable=True, index=True)

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    folhas: Mapped[list["Folha"]] = relationship(back_populates="evento")


class Folha(Base):
    """Uma folha digitalizada, e o registro de como ela foi lida."""
    __tablename__ = "folha"
    # Reenviar o mesmo arquivo dobraria a frequência de todo mundo nele — o
    # mesmo estrago de A40, por outra porta. A restrição é do BANCO e não só do
    # código: é ela que segura dois uploads simultâneos do mesmo PDF.
    __table_args__ = (UniqueConstraint("origem_sha256", "pagina"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evento_id: Mapped[int | None] = mapped_column(ForeignKey("evento.id"),
                                                  nullable=True)
    # A chave como foi lida, com '?' no que não deu para ler.
    chave_lida: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # D35, fase 5: o grupo de onde a folha foi enviada (a aba Envio do grupo). A chave lida
    # casa só com os eventos abertos DELE: contradomínio menor, casamento mais seguro (o
    # raciocínio do D07 aplicado à chave). Nulo é o envio geral, que casa com todos os abertos.
    grupo_envio: Mapped[str | None] = mapped_column(
        String(60), ForeignKey("grupo.nome", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True)
    # Como o vínculo foi feito: automatico | confirmado | corrigido | informado.
    # É o que deixa a acurácia da chave recuperável depois: sem isso o vínculo
    # existe e a medição some (D15).
    origem_vinculo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nome_original: Mapped[str] = mapped_column(String(300))
    arquivo: Mapped[str] = mapped_column(Text)

    # Um PDF de escâner traz VÁRIAS folhas, uma por página — que é o formato
    # real das 500 dos Encontros. Cada página vira uma Folha, e o par
    # (origem, página) é o que identifica de onde ela veio.
    origem_sha256: Mapped[str] = mapped_column(String(64))
    pagina: Mapped[int] = mapped_column(Integer, default=1)

    # sha do PNG efetivamente lido. Não é único: duas páginas idênticas são
    # legítimas. Serve para conferir integridade e para rastrear qual imagem
    # produziu qual leitura.
    sha256: Mapped[str] = mapped_column(String(64))

    modelo_folha: Mapped[str] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(20), default="recebida")
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)

    linhas_preenchidas: Mapped[int] = mapped_column(Integer, default=0)
    linhas_anuladas: Mapped[int] = mapped_column(Integer, default=0)

    # o ponto de operação desta leitura — ver o cabeçalho
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrica_nome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    modelo_matricula: Mapped[str | None] = mapped_column(Text, nullable=True)
    modelo_letras: Mapped[str | None] = mapped_column(Text, nullable=True)
    # D23: a regra dos dois canais (τ) e o recorte da célula ("v0" ou "v3") desta leitura.
    # Nulos numa folha lida antes do D23, que foi decidida sem a regra e com o recorte v0.
    tau_nome: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorte: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # D37: o corte da regra do nome que aponta para a pessoa. Nulo na folha lida antes dela.
    corte_acaso: Mapped[float | None] = mapped_column(Float, nullable=True)
    # D25: com qual lista de matriculados esta folha foi lida. Nulo numa folha
    # lida antes das cargas existirem, que foi lida com a planilha de 21/08/2026.
    carga_base_id: Mapped[int | None] = mapped_column(ForeignKey("carga_base.id"),
                                                      nullable=True)
    # Com qual base de pessoas (geral ou do grupo) a folha foi identificada (22/11). θ e δ
    # foram calibrados com 1.334 pessoas, e o tamanho do contradomínio muda o que a margem
    # significa (A26): o resultado de uma folha só se explica sabendo contra quem ela foi lida.
    base_id: Mapped[int | None] = mapped_column(ForeignKey("base_pessoas.id"), nullable=True)
    # E a lista do grupo que valia (A66): só quem está nela sai aceito; a identificação é contra
    # a base acima. Nula sem lista.
    lista_id: Mapped[int | None] = mapped_column(ForeignKey("base_pessoas.id"), nullable=True)

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Quando a folha entrou na fila pela última vez (envio, releitura, troca de escaneamento).
    # É o que delimita o "lote" no progresso da fila: `criado_em` não serve, porque uma folha
    # antiga relida faria o lote parecer começar meses atrás.
    na_fila_desde: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Concluir a revisão é ATO EXPLÍCITO, e não consequência de ter clicado em
    # todas as linhas. É ele que separa "estou no meio disto" de "esta folha
    # está apurada", e é a partir dele que a frequência da folha vale. Sem uma
    # marca assim, ninguém sabe se uma folha sem pendência foi conferida ou
    # apenas não foi aberta ainda.
    revisada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revisada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # A61: a imagem foi trocada por outro escaneamento do mesmo papel. O rótulo
    # (`nome_original` e `pagina`) continua o do lote: é o que diz a quem revisa de qual
    # papel se trata. `origem_sha256` passa a ser o do arquivo novo, então reenviar esse
    # arquivo pela porta normal devolve esta folha em vez de criar uma duplicata.
    # D30: outro escaneamento do mesmo papel. A folha com `copia_de` não conta na frequência
    # nem na quarentena do grupo: conta a folha para onde aponta. `semelhanca_copia` é a fração
    # de linhas que bateram, e `nao_e_copia` é quem revisa dizendo que a detecção errou, para
    # ela não remarcar a folha. Ver `api/reenvio.py`.
    copia_de: Mapped[int | None] = mapped_column(ForeignKey("folha.id"), nullable=True)
    semelhanca_copia: Mapped[float | None] = mapped_column(Float, nullable=True)
    nao_e_copia: Mapped[bool] = mapped_column(Boolean, default=False)
    # D29: o marcador que faltava e cujo canto foi ESTIMADO pelos outros três (0 a 3). Nulo
    # na folha lida com os 4. Viaja junto da leitura porque muda a confiança nela.
    marcador_estimado: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reescaneada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arquivo_reescaneado: Mapped[str | None] = mapped_column(String(300), nullable=True)

    evento: Mapped[Evento | None] = relationship(back_populates="folhas")
    linhas: Mapped[list["Linha"]] = relationship(
        back_populates="folha", cascade="all, delete-orphan")


class Linha(Base):
    """Uma linha preenchida, com a leitura crua e os candidatos ranqueados."""
    __tablename__ = "linha"
    __table_args__ = (UniqueConstraint("folha_id", "numero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folha_id: Mapped[int] = mapped_column(ForeignKey("folha.id"))
    numero: Mapped[int] = mapped_column(Integer)

    leitura_matricula: Mapped[str] = mapped_column(String(20))
    leitura_nome: Mapped[str] = mapped_column(String(200))
    # confiança e motivo POR CÉLULA: é o que a interface pinta em cor, e o que
    # distingue "não escreveu" de "escreveu e não deu para ler" — a distinção
    # que revelou A40
    confiancas_matricula: Mapped[list] = mapped_column(Json, default=list)
    confiancas_nome: Mapped[list] = mapped_column(Json, default=list)
    motivos_matricula: Mapped[list] = mapped_column(Json, default=list)
    motivos_nome: Mapped[list] = mapped_column(Json, default=list)

    score: Mapped[float] = mapped_column(Float, default=0.0)
    margem: Mapped[float] = mapped_column(Float, default=0.0)
    decisao: Mapped[str] = mapped_column(String(20))     # aceito|quarentena|anulada
    matricula_sugerida: Mapped[str | None] = mapped_column(String(20),
                                                           nullable=True)
    candidatos: Mapped[list] = mapped_column(Json, default=list)

    # A40: a mesma pessoa duas vezes na mesma folha não é erro de identificação
    # — as duas linhas apontam para a pessoa certa —, então nem precisão nem
    # cobertura a enxergam. Ela infla a frequência, que é o produto. A marca
    # existe para a revisão humana decidir qual das duas vale.
    duplicada_na_folha: Mapped[bool] = mapped_column(Boolean, default=False)
    # 22/11 (A66): o gate aceitou, mas o campeão não está na lista do grupo, e a linha foi para
    # a quarentena por isso. Gravado na leitura, porque a lista da folha não muda depois.
    fora_da_lista: Mapped[bool] = mapped_column(Boolean, default=False)
    # D37 (A67): o resto do gate passou, e o nome lido casa igual ou melhor com muita gente.
    nome_nao_confirma: Mapped[bool] = mapped_column(Boolean, default=False)

    folha: Mapped[Folha] = relationship(back_populates="linhas")
    # cascade para apagar a folha levar as revisões junto: sem isso a exclusão
    # deixaria revisão órfã apontando para linha que não existe mais, e o
    # expurgo pedido pela LGPD ficaria pela metade
    revisoes: Mapped[list["Revisao"]] = relationship(
        back_populates="linha", cascade="all, delete-orphan")


class Revisao(Base):
    """A decisão humana sobre uma linha. Append-only: revisar de novo é um
    registro novo, e o histórico é o que permite auditar quem mudou o quê."""
    __tablename__ = "revisao"

    id: Mapped[int] = mapped_column(primary_key=True)
    linha_id: Mapped[int] = mapped_column(ForeignKey("linha.id"))
    # nulo = "não identificado", que é decisão legítima e não ausência de
    # decisão. D03: a quarentena nunca descarta, mas o revisor pode não saber.
    matricula_escolhida: Mapped[str | None] = mapped_column(String(20),
                                                            nullable=True)
    # D32: "não está na base". A pessoa estava lá e é legível, mas não está no contradomínio
    # (externo, ou matriculado depois da extração da base). A revisão guarda a matrícula e o
    # nome que quem revisou conferiu, para a presença não sumir da frequência. Com
    # `matricula_informada`, `matricula_escolhida` é nula: são caminhos excludentes.
    matricula_informada: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nome_informado: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revisor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    linha: Mapped[Linha] = relationship(back_populates="revisoes")


class CargaBase(Base):
    """Um envio da lista de matriculados que foi APLICADO (D25).

    A base vigente é "os matriculados na data" (D07), e essa data anda: gente
    entra e sai durante o semestre. Trocar a base muda o que todo resultado
    significa (A10: 22/22 na base nova contra 7/22 na velha), então cada troca
    fica registrada com a data da extração e o que mudou, e cada folha guarda
    com qual carga foi lida.
    """
    __tablename__ = "carga_base"

    id: Mapped[int] = mapped_column(primary_key=True)
    # A data da EXTRAÇÃO da lista, não a do envio. O .xlsx traz no nome da aba;
    # o CSV do SIGAA não traz, e quem envia informa.
    data_referencia: Mapped[date] = mapped_column(Date)
    arquivo_original: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    aba: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # "envio" pela tela, ou "semente": a planilha do repositório registrada na
    # primeira subida do servidor, para as folhas antigas terem a quem apontar.
    origem: Mapped[str] = mapped_column(String(20), default="envio")
    pessoas: Mapped[int] = mapped_column(Integer)
    entraram: Mapped[int] = mapped_column(Integer, default=0)
    voltaram: Mapped[int] = mapped_column(Integer, default=0)
    sairam: Mapped[int] = mapped_column(Integer, default=0)
    mudaram: Mapped[int] = mapped_column(Integer, default=0)
    aplicada_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    aplicada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Participante(Base):
    """O contradomínio: quem pode ser identificado (D07).

    Quem some de uma lista nova NÃO é apagado: fica `ativo=False` (D25). A
    leitura de folhas só compara com os ativos, mas revisões e frequências de
    eventos antigos continuam apontando para a pessoa, com nome. Apagar faria a
    frequência de setembro perder o nome de quem trancou em outubro.
    """
    __tablename__ = "participante"

    matricula: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    # normalizado A-Z + espaço (D05), gravado para a busca não normalizar 1.334
    # nomes a cada tecla digitada na revisão
    nome_normalizado: Mapped[str] = mapped_column(String(200), index=True)
    curso: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # a carga em que deixou de constar; nulo enquanto ativo
    saiu_na_carga: Mapped[int | None] = mapped_column(ForeignKey("carga_base.id"),
                                                      nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime,
                                                    server_default=func.now())


class BaseDePessoas(Base):
    """A base geral do sistema (`grupo` nulo) ou a lista de um grupo (22/11).

    A identificação é contra a geral; a lista do grupo só filtra quem sai aceito, e quem só
    está nela também entra na identificação (A66). Ativa é a que vale agora; a
    substituída ou apagada sai de uso, mas fica no banco enquanto alguma folha apontar para
    ela, para a frequência antiga não perder nomes. Sem folha nenhuma, é apagada de vez.
    """
    __tablename__ = "base_pessoas"

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo: Mapped[str | None] = mapped_column(
        ForeignKey("grupo.nome", onupdate="CASCADE"), nullable=True, index=True)
    arquivo_original: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    aba: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # A data da EXTRAÇÃO da lista, quando se sabe: o .xlsx traz no nome da aba, o CSV do
    # SIGAA não traz e quem envia informa. É o que deixa a prévia avisar de lista velha.
    data_referencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "envio" pela tela, ou "semente": a planilha do repositório, na primeira subida
    origem: Mapped[str] = mapped_column(String(20), default="envio")
    pessoas: Mapped[int] = mapped_column(Integer)
    enviada_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    enviada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    retirada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PessoaDaBase(Base):
    """Uma pessoa numa base. A mesma matrícula pode estar em várias bases, com o nome de cada."""
    __tablename__ = "pessoa_base"

    base_id: Mapped[int] = mapped_column(ForeignKey("base_pessoas.id", ondelete="CASCADE"),
                                         primary_key=True)
    matricula: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    # normalizado A-Z + espaço (D05), gravado uma vez: a busca da revisão e a leitura não
    # normalizam a base a cada uso
    nome_normalizado: Mapped[str] = mapped_column(String(200), index=True)
    curso: Mapped[str | None] = mapped_column(String(200), nullable=True)
