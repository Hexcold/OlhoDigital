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

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB no Postgres, JSON em qualquer outro dialeto. A variante existe para o
# esquema continuar compilável fora do Postgres — é o que permite conferir as
# definições sem um servidor no ar.
Json = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


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

    id: Mapped[int] = mapped_column(primary_key=True)

    # A junção impressa no papel. Única porque duas ocorrências com a mesma
    # chave são indistinguíveis para o sistema — a folha iria para as duas.
    # A–Z apenas (D14): é o que deixa a chave ser lida pelo classificador de 26
    # classes sem trazer de volta as confusões 0/O, 5/S, 2/Z e 1/I.
    chave: Mapped[str] = mapped_column(String(16), unique=True, index=True)
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

    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Concluir a revisão é ATO EXPLÍCITO, e não consequência de ter clicado em
    # todas as linhas. É ele que separa "estou no meio disto" de "esta folha
    # está apurada", e é a partir dele que a frequência da folha vale. Sem uma
    # marca assim, ninguém sabe se uma folha sem pendência foi conferida ou
    # apenas não foi aberta ainda.
    revisada_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revisada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)

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
    revisor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    linha: Mapped[Linha] = relationship(back_populates="revisoes")


class Participante(Base):
    """O contradomínio: quem pode ser identificado (D07).

    Hoje uma tabela só, sem versão. A base vigente é "os matriculados NA DATA
    da coleta" — 1.334 em 21/08/2026 —, e trocar a base muda o que todo
    resultado significa (A10: 22/22 na base nova contra 7/22 na velha).
    Versionar isto é pendência conhecida, não esquecimento.
    """
    __tablename__ = "participante"

    matricula: Mapped[str] = mapped_column(String(20), primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    # normalizado A-Z + espaço (D05), gravado para a busca não normalizar 1.334
    # nomes a cada tecla digitada na revisão
    nome_normalizado: Mapped[str] = mapped_column(String(200), index=True)
    curso: Mapped[str | None] = mapped_column(String(200), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime,
                                                    server_default=func.now())
