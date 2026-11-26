"""Upload e leitura de folhas.

O upload **não processa e responde**: a leitura leva ~9 s por folha (medido em
30/08: 26,5 s para três), o que não cabe numa requisição HTTP e muito menos em
500. Então `POST /folhas` grava, enfileira e devolve as folhas criadas; o
cliente acompanha por `GET /folhas/{id}` até o estado virar `processada`.

Um envio pode criar **várias** folhas: o escâner entrega PDF, e cada página é
uma folha. Por isso a rota devolve uma lista mesmo quando o arquivo é uma
imagem só — o contrato não muda conforme o que foi enviado.

`BackgroundTasks` do FastAPI basta aqui, e a escolha é deliberada: Celery e
Redis resolveriam o mesmo problema somando dois serviços ao caminho de
reprodução, contra a promessa de execução local da pergunta de pesquisa. O
paralelismo que eles trariam seria inútil de qualquer jeito — o pipeline não é
reentrante e já roda serializado (ver `pipeline._TRAVA`).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, Request, UploadFile)
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from .. import bases, documentos, pipeline, reenvio
from ..config import config
from ..db import dependencia_sessao, sessao
from ..sessao import usuario_logado
from ..esquemas import (EventoSugerido, FilaSaida, FolhaSaida, LinhaSaida, PedidoApagarFolhas,
                        PreviaApagar, REVISOR_AUTOMATICO, ResumoRevisao, SugestaoEventos,
                        VinculoEntrada, montar_folha, montar_linha, sem_matricula,
                        ultima_revisao)
from ..tabelas import Evento, Folha, Grupo, Linha, PessoaDaBase, Revisao

rotas = APIRouter(prefix="/folhas", tags=["folhas"])
log = logging.getLogger("olhodigital")

# Decisão de gente nunca é desfeita por uma releitura.
# "removido" (D34): alguém tirou a folha do evento de propósito. Antes gravava nulo, igual à
# folha que caiu na bandeja sozinha, e reler ou reabrir um evento a religava por cima.
VINCULO_HUMANO = ("informado", "confirmado", "corrigido", "removido")


def _eventos_abertos(db: Session, grupo: str | None = None) -> list[Evento]:
    """O contradomínio da chave: encerrar um evento o tira daqui (D15). Com `grupo` (a folha
    veio da aba Envio dele, D35 fase 5), só os eventos abertos do grupo."""
    consulta = select(Evento).where(Evento.situacao == "em andamento")
    if grupo is not None:
        consulta = consulta.where(Evento.grupo == grupo)
    return list(db.scalars(consulta))


def _vincular_evento(db: Session, folha: Folha) -> None:
    """Lê a chave e liga a folha ao evento, se o gate deixar (D17).

    Roda ANTES da identificação desde 22/11, porque o grupo do evento escolhe a lista
    de pessoas. Falhar aqui não derruba a folha: sem evento, vale a lista do grupo de onde
    ela foi enviada (ou nenhuma), e a folha vai para a bandeja, onde folha sem vínculo deve estar.
    """
    try:
        folha.chave_lida = pipeline.ler_chave(Path(folha.arquivo),
                                              folha.modelo_folha)
    except Exception as e:                                  # noqa: BLE001
        log.warning("folha %s: chave não lida (%s: %s)", folha.id,
                    type(e).__name__, e)
        folha.chave_lida = None
    if folha.origem_vinculo in VINCULO_HUMANO:
        return
    _ligar_pela_chave(db, folha)


def _ligar_pela_chave(db: Session, folha: Folha) -> None:
    """Casa a chave JÁ LIDA com os eventos abertos e liga a folha, se o gate deixar (D17).

    Separada da leitura para religar sem reabrir a imagem (D34): ao reabrir um evento, as
    folhas que caíram em "sem evento" enquanto ele estava encerrado voltam para ele.
    """
    abertos = {e.chave: e.id for e in _eventos_abertos(db, folha.grupo_envio)}
    achado = pipeline.casar_chave(folha.chave_lida, list(abertos))
    # D33: chave reaproveitada (já foi de um evento encerrado) não liga sozinha. A folha
    # pode ser uma atrasada do evento antigo, e iria em silêncio para o novo. Vai para a
    # bandeja, com o evento aberto como sugestão e o aviso de quem teve a chave antes.
    if achado.chave and _encerrados_com_a_chave(db, achado.chave):
        folha.evento_id = folha.origem_vinculo = None
        return
    folha.evento_id = abertos.get(achado.chave)
    folha.origem_vinculo = "automatico" if achado.chave else None


def religar_bandeja(db: Session, chaves: set[str]) -> int:
    """Religa as folhas sem evento cuja chave lida casa com uma destas chaves (D34).

    É o desfazer de um encerramento por engano: enquanto o evento estava encerrado, uma folha
    enviada não achava o evento e caía em "sem evento". Só mexe nas que foram para lá
    sozinhas (`origem_vinculo` nulo): vínculo decidido por alguém não é tocado. A regra da
    chave reaproveitada (D33) continua valendo. Devolve quantas foram religadas.
    """
    if not chaves:
        return 0
    religadas = 0
    for folha in db.scalars(select(Folha).where(
            Folha.evento_id.is_(None), Folha.origem_vinculo.is_(None),
            Folha.estado == "processada", Folha.chave_lida.is_not(None))):
        achado = pipeline.casar_chave(folha.chave_lida, sorted(chaves))
        if not achado.chave:
            continue
        _ligar_pela_chave(db, folha)
        if folha.evento_id is not None:
            db.flush()
            reenvio.detectar(db, folha)               # D30: pode ser cópia de outra folha
            religadas += 1
    return religadas


def _encerrados_com_a_chave(db: Session, chave: str) -> list[Evento]:
    """Os eventos encerrados que já usaram esta chave, do mais recente ao mais antigo."""
    return list(db.scalars(select(Evento).where(Evento.chave == chave,
                                                Evento.situacao == "encerrado")
                           .order_by(Evento.encerrado_em.desc().nullslast())))


# Separa, no texto do erro, a frase para quem usa do detalhe técnico (a tela o esconde).
DETALHE = "\n\ndetalhe técnico: "


def _ler_e_gravar(folha_id: int) -> None:
    """Roda o pipeline e grava as linhas. Executa fora da requisição.

    Abre a PRÓPRIA sessão: a da requisição já foi fechada quando isto roda.
    """
    db = sessao()
    try:
        # Reivindica a folha num UPDATE só: de "recebida" para "processando". Se duas leituras
        # tentarem a mesma folha (a fila retomada ao subir e um "reavaliar", ou dois servidores
        # durante um reinício, como em 20/11), só uma consegue, e a outra desiste aqui. Antes a
        # checagem era no Python, e as duas passavam.
        reivindicada = db.execute(
            update(Folha).where(Folha.id == folha_id, Folha.estado == "recebida")
            .values(estado="processando")).rowcount
        db.commit()
        if not reivindicada:
            return
        folha = db.get(Folha, folha_id)

        caminho = Path(folha.arquivo)
        # Antes de qualquer coisa: isto é uma folha de presença? Quem envia 500
        # arquivos precisa distinguir "mandei o arquivo errado" de "a leitura
        # falhou", e as duas coisas viravam o mesmo traceback do OpenCV.
        encontrados = pipeline.conferir_marcadores(caminho)
        # D29: com 3, a folha é lida com o quarto canto estimado e leva a marca de qual foi.
        # Medido: 0 aceite com outra pessoa nas 59 folhas com o id 0 escondido, e a p28 da
        # coleta bateu 20/20 com as outras duas passadas do mesmo papel.
        folha.marcador_estimado = None
        if encontrados == 3:
            folha.marcador_estimado = min(set(pipeline.CANTOS)
                                          - pipeline.marcadores_encontrados(caminho))
        elif encontrados < 4:
            folha.estado = "nao_e_folha"
            folha.erro = pipeline.mensagem_marcadores(
                pipeline.marcadores_encontrados(caminho))
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return

        # A chave antes da identificação (22/11): é o evento que diz o grupo, e o grupo diz
        # contra qual base de pessoas ler — a dele, ou a geral (`api/bases.py`).
        _vincular_evento(db, folha)
        # A66: a lista do grupo filtra quem sai aceito; a identificação é contra a geral
        base, lista = bases.para_identificar(db, bases.grupo_da_folha(db, folha))
        if base is None:
            folha.estado = "erro"
            folha.erro = ("nenhuma base de pessoas para comparar: envie a lista de matriculados "
                          "na tela inicial (base geral) ou na aba do grupo")
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return
        folha.base_id = base.id
        folha.lista_id = lista.id if lista else None

        try:
            recursos = pipeline.carregar_recursos()
            linhas = pipeline.processar(caminho, folha.modelo_folha,
                                        bases.para_leitura(db, folha.base_id, folha.lista_id))
            pipeline.recortar_para_revisao(caminho, folha.modelo_folha)
        except Exception as e:                      # noqa: BLE001
            # A folha não some e o erro fica no registro: com 500 folhas, uma
            # que falha em silêncio é uma presença perdida sem rastro.
            folha.estado = "erro"
            # A frase é para quem usa; o detalhe, para quem mantém. A tela mostrava só a
            # exceção crua ("ValueError: '/tmp/…' is not in the subpath…", A63).
            folha.erro = (f"A leitura falhou por um problema do sistema, não da folha. Tente "
                          f"\"reavaliar\"; se repetir, guarde o detalhe técnico."
                          f"{DETALHE}{type(e).__name__}: {e}")
            # a hora entra na conta do lote no progresso da fila: sem ela, a folha que falhou
            # não teria como dizer quando saiu da fila
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return

        na_lista = bases.matriculas(db, lista.id) if lista else None
        for dados in linhas:
            # A66: o gate aceitou alguém que não está na lista do grupo — quarentena. Com a
            # geral identificando, quem assinou sem estar na lista é reconhecido como ele mesmo
            # e para aqui, em vez de virar o mais parecido da lista (Experimento 04, parte 2).
            if (na_lista is not None and dados["decisao"] == "aceito"
                    and dados.get("matricula_sugerida") not in na_lista):
                dados = {**dados, "decisao": "quarentena", "fora_da_lista": True}
            linha = Linha(folha_id=folha.id, **dados)
            # D26: só o nome, sem dígito lido na matrícula. Fica na quarentena, à vista,
            # mas já decidida como "não identificado": presença sem matrícula não se
            # credita sem um humano escolher a pessoa, e a folha não trava esperando.
            if dados["decisao"] == "quarentena" and sem_matricula(dados["motivos_matricula"]):
                linha.revisoes.append(Revisao(matricula_escolhida=None,
                                              revisor=REVISOR_AUTOMATICO))
            db.add(linha)

        folha.linhas_preenchidas = len(linhas)
        folha.linhas_anuladas = sum(1 for l in linhas if l["decisao"] == "anulada")

        # PORTÃO CONTRA GEOMETRIA ERRADA.
        #
        # Recortar com o modelo errado não falha: produz a grade inteira cheia
        # de lixo e diz "processada". Medido em 30/08, subindo folhas antigas
        # como `gerada_v2`: m3_2 deu 33 linhas (tem 22) com 0 aceitos, m4_01
        # deu 33 (tem 7) com 0 aceitos, m1_1 deu 33 com 0. Uma folha lida com a
        # geometria certa aceita a grande maioria — a m7_02 fez 14 de 16.
        #
        # Zero aceitos com várias linhas é a assinatura. Não é prova (uma folha
        # pode legitimamente ser toda ambígua), por isso o estado é `suspeita` e
        # não `erro`: os dados ficam, a revisão continua possível, e ninguém
        # confunde isto com leitura boa.
        #
        # Havia um segundo sinal, a grade INTEIRA vir preenchida, que pegava o
        # quase-acerto: a m7_02 lida como `gerada_v1` deu 39 linhas e ainda teve
        # aceites (A43). Saiu em 18/11/2026, por decisão do autor (D21). Nos
        # Encontros, folha cheia é o caso normal de evento lotado, e o sinal a
        # marcava como suspeita, o que a deixava sem evento e sem ponto de
        # operação gravado. O quase-acerto volta a passar sem alarme. O risco
        # dele é ler folha de versão antiga, e a coleta é toda v4.
        aceitos = sum(1 for l in linhas if l["decisao"] == "aceito")
        motivo = ""
        if len(linhas) >= 5 and aceitos == 0:
            motivo = f"{len(linhas)} linhas e nenhum aceite"
        if motivo:
            # Como antes de 22/11: a suspeita não fica ligada sozinha a evento nenhum. A ligação
            # subiu para antes da leitura só para escolher a base; a humana fica como está.
            if folha.origem_vinculo == "automatico":
                folha.evento_id = folha.origem_vinculo = None
            folha.estado = "suspeita"
            folha.erro = (
                f"{motivo} — assinatura de recorte com a geometria errada. "
                f"Esta folha foi lida como '{folha.modelo_folha}'; confira se é "
                "essa a versão dela e releia com a certa se não for.")
            folha.processada_em = datetime.now(timezone.utc)
            db.commit()
            return
        ponto = recursos["ponto"]
        folha.alpha = ponto["alpha"]
        folha.theta = ponto["theta"]
        folha.delta = ponto["delta"]
        folha.metrica_nome = ponto["metrica_nome"]
        folha.tau_nome = ponto["tau_nome"]
        folha.recorte = ponto["recorte"]
        # `get`: um ponto sem a regra (os dublês dos testes) é folha lida sem ela
        folha.corte_acaso = ponto.get("corte_acaso")
        folha.modelo_matricula = recursos["modelo_matricula"]
        folha.modelo_letras = recursos["modelo_letras"]
        folha.estado = "processada"
        folha.processada_em = datetime.now(timezone.utc)
        db.flush()
        # D30: outro escaneamento de uma folha que já está no evento? A cópia não conta.
        reenvio.detectar(db, folha)
        # relida com outra base, a antiga pode ter perdido a última folha que a guardava
        bases.limpar_retiradas(db)
        db.commit()
    finally:
        db.close()


@rotas.post("", response_model=list[FolhaSaida], status_code=201)
async def enviar(tarefas: BackgroundTasks, resposta: Response,
                 arquivo: UploadFile = File(...),
                 evento_id: int | None = Form(None),
                 modelo_folha: str = Form(config.modelo_folha_padrao),
                 grupo: str | None = Form(None),
                 db: Session = Depends(dependencia_sessao)) -> list[Folha]:
    # D35, fase 5: enviado pela aba do grupo, casa só com os eventos dele
    if grupo is not None and db.scalar(select(Grupo.id).where(Grupo.nome == grupo)) is None:
        raise HTTPException(404, f"grupo {grupo!r} não existe")
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "arquivo vazio")

    origem = documentos.sha(conteudo)
    # Reenviar o mesmo arquivo dobraria a frequência de todo mundo nele.
    # Devolve o que já existe em vez de criar — e o banco tem a mesma
    # restrição, para dois uploads simultâneos não passarem os dois.
    ja = list(db.scalars(select(Folha).where(Folha.origem_sha256 == origem)
                         .order_by(Folha.pagina)))
    if ja:
        # 200 e não 201: nada foi criado. É o que deixa a tela dizer "este arquivo já tinha
        # sido enviado" — antes o reenvio voltava igual a um envio novo, e ninguém sabia
        resposta.status_code = 200
        return ja

    try:
        # Numa thread, NUNCA no laço de eventos: esta rota é `async`, e o pdftoppm leva um ou
        # dois minutos num PDF de 60 páginas. Chamado direto, ele parava o servidor inteiro
        # durante a conversão, e nem a página inicial carregava (20/11, uma aba nova em
        # branco enquanto o envio convertia).
        imagens = await run_in_threadpool(documentos.paginas, conteudo,
                                          config.pasta_folhas, origem)
    except RuntimeError as e:
        raise HTTPException(422, str(e)) from e

    criadas = []
    # a mesma hora para o envio inteiro: é um lote só (ver `fila`)
    entrada = datetime.now(timezone.utc)
    for numero, imagem in enumerate(imagens, start=1):
        folha = Folha(
            evento_id=evento_id,
            origem_vinculo="informado" if evento_id is not None else None,
            nome_original=arquivo.filename or "sem nome",
            arquivo=str(imagem),
            origem_sha256=origem,
            pagina=numero,
            sha256=documentos.sha(imagem.read_bytes()),
            modelo_folha=modelo_folha,
            estado="recebida",
            na_fila_desde=entrada,
            grupo_envio=grupo,
        )
        db.add(folha)
        criadas.append(folha)
    db.commit()

    # um pedido de leitura para o envio inteiro: `ler_fila` pega as folhas na ordem da fila
    tarefas.add_task(ler_fila)
    return criadas


def retomar_fila(db: Session) -> list[int]:
    """O que ficou pela metade quando o servidor parou, de volta à fila. Chamado ao subir.

    A fila de leitura vive na memória (BackgroundTasks), então um reinício no meio de um envio
    deixava folhas em "recebida" e "processando" para sempre, sem aviso: foram 79 em 20/11.
    "processando" SEM linhas é leitura interrompida, e volta. COM linhas não deveria existir
    (linhas e estado são gravados no mesmo commit); se existir, fica como está e vai para o
    log, em vez de o servidor adivinhar o que aconteceu.

    Devolve os ids a ler, em ordem.
    """
    agora = datetime.now(timezone.utc)
    for folha in db.scalars(select(Folha).where(Folha.estado == "processando")):
        if folha.linhas:
            log.warning("folha %s está em 'processando' com %s linhas gravadas; não foi "
                        "retomada", folha.id, len(folha.linhas))
            continue
        folha.estado = "recebida"
        folha.na_fila_desde = folha.na_fila_desde or agora
    for folha in db.scalars(select(Folha).where(Folha.estado == "recebida",
                                                Folha.na_fila_desde.is_(None))):
        folha.na_fila_desde = agora
    db.commit()
    return list(db.scalars(select(Folha.id).where(Folha.estado == "recebida")
                           .order_by(Folha.id)))


def ler_em_sequencia(ids: list[int]) -> None:
    """Lê as folhas dadas, uma por vez, sem deixar uma falha parar as outras."""
    for folha_id in ids:
        try:
            _ler_e_gravar(folha_id)
        except Exception:                                  # noqa: BLE001
            # uma folha que falha não para a fila: o erro fica no log, e as outras seguem
            log.exception("falha ao ler a folha %s na fila", folha_id)


# Um leitor só no processo inteiro. Antes, cada "reavaliar" ou "trocar escaneamento" agendava
# a sua própria leitura, e elas rodavam em paralelo: cada uma marcava a folha "processando" e
# esperava a trava do pipeline SEGURANDO uma conexão do banco. Com 15 esperando, o pool
# (5 + 10) acabou, e toda requisição — o login, a barra de progresso — esperava 30 s e caía
# em 500 (bateria de 22/11, 61 releituras pedidas em sequência).
_LEITOR = threading.Lock()


def _proxima_na_fila(ja_tentadas: set[int]) -> int | None:
    """A folha que espera há mais tempo, numa sessão curta que devolve a conexão na hora."""
    with sessao() as db:
        return db.scalar(
            select(Folha.id).where(Folha.estado == "recebida", Folha.id.not_in(ja_tentadas))
            .order_by(Folha.na_fila_desde.asc().nulls_last(), Folha.id).limit(1))


def ler_fila() -> None:
    """Lê tudo que está "recebida", se ninguém estiver lendo; senão volta na hora.

    Quem chega com a fila já sendo lida não espera nem segura conexão: o leitor que está
    rodando pega a folha nova quando terminar a atual. `ja_tentadas` impede o laço de
    girar para sempre numa folha que continua "recebida" (uma leitura que falhou antes de
    reivindicá-la). Depois de soltar a trava, confere de novo: uma folha que entrou no
    instante entre "não há mais nada" e soltar ficaria parada até o próximo envio.
    """
    ja_tentadas: set[int] = set()
    while True:
        if not _LEITOR.acquire(blocking=False):
            return
        try:
            while (folha_id := _proxima_na_fila(ja_tentadas)) is not None:
                ja_tentadas.add(folha_id)
                ler_em_sequencia([folha_id])
        finally:
            _LEITOR.release()
        if _proxima_na_fila(ja_tentadas) is None:
            return


def _prontas_do_lote(db: Session, entradas_pendentes: list[datetime]) -> int:
    """Quantas folhas do lote atual já saíram da fila.

    O lote é o período em que a fila está ocupada SEM PARAR até agora. Começa na entrada
    mais antiga das que faltam, e recua sempre que uma folha já lida ainda estava na fila
    depois desse começo (ela entrou antes e saiu depois). Assim, o total fica fixo e as
    prontas sobem, mesmo com vários arquivos enviados em sequência; um lote anterior,
    separado por um intervalo com a fila vazia, não entra.

    A primeira versão começava o lote na entrada mais antiga das PENDENTES, e as folhas do
    mesmo envio já lidas, que tinham entrado microssegundos antes, ficavam de fora: "0 de 33",
    "0 de 23"... (o autor, em 20/11).

    Só compara horas gravadas no banco entre si, então o fuso do Postgres não importa.
    """
    if not entradas_pendentes:
        return 0
    inicio = min(entradas_pendentes)
    saidas = list(db.execute(
        select(Folha.na_fila_desde, Folha.processada_em)
        .where(Folha.estado.not_in(("recebida", "processando")),
               Folha.na_fila_desde.is_not(None))
        .order_by(Folha.na_fila_desde.desc())))
    while True:
        antes = [e for e, s in saidas if e < inicio and s is not None and s >= inicio]
        if not antes:
            break
        inicio = min(antes)
    return sum(1 for e, _ in saidas if e >= inicio)


def _ritmo(db: Session) -> float:
    """Segundos por folha: a mediana entre leituras seguidas, nas últimas 20 da última hora.

    Intervalo acima de 2 min é pausa entre envios, não ritmo de leitura, e fica de fora. Sem
    histórico, 9 s, que é o medido na coleta.
    """
    # Sem relógio de parede: a "última hora" conta a partir da leitura mais recente. Comparar
    # com o agora do Python errava 3 h, porque o banco guarda a hora no fuso dele
    # (Brazil/East) e o Python a gera em UTC: nenhuma leitura entrava e o ritmo caía nos 9 s.
    # Sem as folhas em erro: uma falha leva milissegundos, e um lote inteiro que falhou
    # pôs a mediana em 0 s e a tela em "0 s por folha" durante a releitura.
    recentes = list(db.scalars(
        select(Folha.processada_em).where(Folha.processada_em.is_not(None),
                                          Folha.estado != "erro")
        .order_by(Folha.processada_em.desc()).limit(21)))
    momentos = sorted(t for t in recentes if recentes and t >= recentes[0] - timedelta(hours=1))
    passos = sorted(b.timestamp() - a.timestamp() for a, b in zip(momentos, momentos[1:])
                    if 0 < b.timestamp() - a.timestamp() <= 120)
    if not passos:
        return 9.0
    meio = len(passos) // 2
    return passos[meio] if len(passos) % 2 else (passos[meio - 1] + passos[meio]) / 2


@rotas.get("/fila", response_model=FilaSaida)
def fila(db: Session = Depends(dependencia_sessao)) -> dict:
    """O progresso da leitura: quantas faltam, quantas do lote já estão prontas e quanto falta.

    Uma consulta por pergunta da tela, em vez de a tela perguntar folha por folha a cada
    segundo (com 62 folhas na fila, eram 62 requisições por segundo).
    """
    pendentes = list(db.execute(select(Folha.estado, Folha.na_fila_desde)
                                .where(Folha.estado.in_(("recebida", "processando")))))
    na_fila = sum(1 for e, _ in pendentes if e == "recebida")
    lendo = len(pendentes) - na_fila
    prontas = _prontas_do_lote(db, [d for _, d in pendentes if d is not None])
    agora = db.scalars(select(Folha).where(Folha.estado == "processando")
                       .order_by(Folha.id)).first()
    ritmo = _ritmo(db)
    return {"na_fila": na_fila, "lendo": lendo,
            "lote_total": len(pendentes) + prontas, "lote_prontas": prontas,
            "lendo_agora": f"{agora.nome_original}, página {agora.pagina}" if agora else None,
            "segundos_por_folha": round(ritmo, 1),
            "estimativa_s": round(ritmo * len(pendentes))}


def _contadores(db: Session, ids: list[int]) -> dict[int, tuple[int, int]]:
    """Pendências e repetidas por folha, em UMA consulta.

    Sem isto a lista faria uma consulta por folha, e com 500 folhas a tela de
    abertura viraria 500 idas ao banco para desenhar uma etiqueta.

    Pendente é linha de quarentena **sem nenhuma revisão**. A tabela de revisão
    é append-only, então existir qualquer registro já significa que alguém
    decidiu — o que vale é o mais recente, e isso é problema de quem lê a linha.
    """
    if not ids:
        return {}
    decididas = select(Revisao.linha_id).distinct().scalar_subquery()
    linhas = db.execute(
        select(
            Linha.folha_id,
            func.count().filter((Linha.decisao == "quarentena")
                                & Linha.id.not_in(decididas)),
            func.count().filter(Linha.duplicada_na_folha.is_(True)),
        ).where(Linha.folha_id.in_(ids)).group_by(Linha.folha_id)
    ).all()
    return {fid: (pend, dup) for fid, pend, dup in linhas}


@rotas.get("", response_model=list[FolhaSaida])
def listar(evento_id: int | None = None, grupo: str | None = None,
           db: Session = Depends(dependencia_sessao)) -> list[dict]:
    # o evento vem junto: com 500 folhas, buscá-lo por folha viraria 500 consultas
    q = (select(Folha).options(selectinload(Folha.evento))
         .order_by(Folha.criado_em.desc(), Folha.pagina))
    if evento_id is not None:
        q = q.where(Folha.evento_id == evento_id)
    if grupo is not None:                                  # D35: a aba de envio do grupo
        # as do grupo e as que vieram pela aba dele e ainda não acharam evento (fase 5)
        q = (q.outerjoin(Evento, Folha.evento_id == Evento.id)
             .where(or_(Evento.grupo == grupo,
                        (Folha.evento_id.is_(None)) & (Folha.grupo_envio == grupo))))
    folhas = list(db.scalars(q))
    contagem = _contadores(db, [f.id for f in folhas])
    return [montar_folha(f, *contagem.get(f.id, (0, 0))) for f in folhas]


# Estados em que a folha ainda está sendo lida em segundo plano: ela nunca é apagada em massa
# no meio da leitura, que terminaria gravando numa folha que não existe mais.
NA_FILA = ("recebida", "processando")


def selecionar_para_apagar(folhas: list[Folha], incluir_revisadas: bool
                           ) -> tuple[list[Folha], int, int]:
    """(as que saem, revisadas que ficam, na fila que ficam) — a regra do apagar em massa.

    A folha concluída carrega a frequência apurada, e por isso, como no apagar de uma folha
    só, sai apenas a pedido explícito (D24).
    """
    na_fila = [f for f in folhas if f.estado in NA_FILA]
    revisadas = [f for f in folhas if f.estado not in NA_FILA and f.revisada_em is not None]
    saem = [f for f in folhas if f.estado not in NA_FILA
            and (f.revisada_em is None or incluir_revisadas)]
    return saem, (0 if incluir_revisadas else len(revisadas)), len(na_fila)


def apagar_folhas(db: Session, folhas: list[Folha]) -> int:
    """Apaga folhas, suas linhas, suas revisões e os arquivos em disco.

    Os arquivos saem DEPOIS do commit: se o banco falhar, o dado continua íntegro. Na ordem
    inversa, um erro deixaria registro apontando para imagem que não existe mais.
    """
    imagens = [Path(f.arquivo) for f in folhas]
    # D30: quem era cópia de uma folha que sai passa a contar (ou vira cópia de outra que fica)
    saem = {f.id for f in folhas}
    for folha in folhas:
        reenvio.soltar(db, folha)
    db.flush()
    soltas = list(db.scalars(select(Folha).where(Folha.copia_de.in_(saem),
                                                 Folha.id.not_in(saem))))
    for folha in soltas:
        folha.copia_de = folha.semelhanca_copia = None
    for folha in folhas:
        db.delete(folha)          # linhas e revisões saem por cascade
    db.flush()
    for folha in soltas:
        reenvio.detectar(db, folha)
    db.commit()
    for imagem in imagens:
        shutil.rmtree(pipeline.pasta_recortes(imagem), ignore_errors=True)
        imagem.unlink(missing_ok=True)
    return len(imagens)


def apagar_em_massa(db: Session, folhas: list[Folha], pedido: PedidoApagarFolhas) -> dict:
    """Confere o número digitado contra a conta do servidor e só então apaga.

    O número protege de duas coisas: do clique sem querer, e de apagar mais do que a pessoa
    viu — se uma folha chegou entre a prévia e o pedido, a conta muda e nada sai.
    """
    saem, revisadas, na_fila = selecionar_para_apagar(folhas, pedido.incluir_revisadas)
    if pedido.confirmacao != len(saem):
        raise HTTPException(
            409, f"a confirmação foi {pedido.confirmacao}, mas são {len(saem)} folha(s) "
                 "a apagar agora; confira de novo antes de apagar")
    return {"apagar": apagar_folhas(db, saem), "revisadas_mantidas": revisadas,
            "na_fila_mantidas": na_fila}


@rotas.get("/apagar-todas", response_model=PreviaApagar)
def previa_apagar_todas(incluir_revisadas: bool = False,
                        db: Session = Depends(dependencia_sessao)) -> dict:
    """Quantas folhas o apagar-todas vai tirar, e quantas ficam. Não apaga nada."""
    saem, revisadas, na_fila = selecionar_para_apagar(list(db.scalars(select(Folha))),
                                                      incluir_revisadas)
    return {"apagar": len(saem), "revisadas_mantidas": revisadas, "na_fila_mantidas": na_fila}


@rotas.post("/apagar-todas", response_model=PreviaApagar)
def apagar_todas(pedido: PedidoApagarFolhas,
                 db: Session = Depends(dependencia_sessao)) -> dict:
    """Apaga todas as folhas do sistema, menos as revisadas (salvo pedido) e as na fila.

    Os eventos ficam: apagar folha não apaga a ocorrência que ela registrou.
    """
    return apagar_em_massa(db, list(db.scalars(select(Folha))), pedido)


@rotas.get("/{folha_id}", response_model=FolhaSaida)
def obter(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    return montar_folha(folha, *_contadores(db, [folha_id]).get(folha_id, (0, 0)))


@rotas.delete("/{folha_id}", status_code=204)
def apagar(folha_id: int, forcar: bool = False,
           db: Session = Depends(dependencia_sessao)) -> None:
    """Remove a folha, suas linhas, suas revisões e os arquivos em disco.

    Apagar de verdade, e não marcar como removida, é o que a LGPD pede de um
    sistema que guarda matrícula e nome manuscritos: se a pessoa pedir a
    exclusão, o dado tem que sair do disco, não virar linha oculta.

    **Folha já revisada exige `forcar`.** Ela carrega a frequência apurada de
    quem assinou, e apagá-la por engano destrói registro de presença — que é o
    produto do sistema. O clique a mais é barato perto disso.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.revisada_em is not None and not forcar:
        raise HTTPException(
            409, "esta folha já foi revisada e carrega frequência apurada; "
                 "repita com forcar=true se for mesmo para apagar")
    apagar_folhas(db, [folha])


@rotas.get("/{folha_id}/imagem")
def imagem(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """A folha inteira, para quando o recorte da linha não basta."""
    folha = db.get(Folha, folha_id)
    if folha is None or not Path(folha.arquivo).exists():
        raise HTTPException(404, "imagem não encontrada")
    return FileResponse(folha.arquivo, media_type="image/png")


@rotas.get("/{folha_id}/chave.jpg")
def recorte_chave(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """A banda da chave como foi escaneada: é o que a bandeja mostra."""
    folha = db.get(Folha, folha_id)
    caminho = folha and pipeline.caminho_tira_chave(Path(folha.arquivo))
    if not caminho or not caminho.exists():
        raise HTTPException(404, "recorte da chave não gerado para esta folha")
    return FileResponse(caminho, media_type="image/jpeg")


def _sugestao(db: Session, folha: Folha) -> SugestaoEventos:
    abertos = {e.chave: e for e in _eventos_abertos(db, folha.grupo_envio)}
    achado = pipeline.casar_chave(folha.chave_lida, list(abertos))
    candidatos = [EventoSugerido(id=abertos[c].id, chave=c, nome=abertos[c].nome, distancia=d)
                  for c, d in achado.candidatas]
    aviso, motivo = None, achado.explicacao
    antigos = _encerrados_com_a_chave(db, candidatos[0].chave) if candidatos else []
    if antigos:
        a = antigos[0]
        quando = f", encerrado em {a.encerrado_em:%d/%m/%Y}" if a.encerrado_em else ""
        grupo = f" (grupo {a.grupo})" if a.grupo else ""
        aviso = (f"A chave {a.chave} já foi do evento \"{a.nome}\"{grupo}{quando}. Esta folha "
                 "pode ser uma atrasada daquele evento: confira o dia no carimbo antes de "
                 "confirmar.")
        motivo = "chave reaproveitada de um evento encerrado: confirme o evento"
    return SugestaoEventos(motivo=motivo, candidatos=candidatos, aviso=aviso)


@rotas.get("/{folha_id}/eventos-sugeridos", response_model=SugestaoEventos)
def eventos_sugeridos(folha_id: int,
                      db: Session = Depends(dependencia_sessao)) -> SugestaoEventos:
    """Os eventos abertos, do mais provável ao menos provável, para a bandeja."""
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    return _sugestao(db, folha)


@rotas.post("/{folha_id}/evento", response_model=FolhaSaida)
def vincular_evento(folha_id: int, dados: VinculoEntrada,
                    db: Session = Depends(dependencia_sessao)) -> dict:
    """A decisão humana sobre o evento da folha.

    `confirmado` quando concorda com o que a máquina apontava, `corrigido`
    quando discorda. É essa diferença que permite medir a chave depois.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if dados.evento_id is None:
        folha.evento_id = None
        folha.origem_vinculo = "removido"
    else:
        evento = db.get(Evento, dados.evento_id)
        if evento is None:
            raise HTTPException(404, "evento não encontrado")
        candidatos = _sugestao(db, folha).candidatos
        apontado = folha.evento_id or (candidatos[0].id if candidatos else None)
        folha.origem_vinculo = ("confirmado" if evento.id == apontado
                                else "corrigido")
        folha.evento_id = evento.id
    # D30: a relação de cópia é dentro do evento; mudou o evento, ela é refeita
    reenvio.soltar(db, folha)
    db.flush()
    reenvio.detectar(db, folha)
    db.commit()
    return obter(folha_id, db)


@rotas.get("/{folha_id}/linhas", response_model=list[LinhaSaida])
def linhas(folha_id: int,
           db: Session = Depends(dependencia_sessao)) -> list[dict]:
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    achadas = list(db.scalars(select(Linha).where(Linha.folha_id == folha_id)
                              .order_by(Linha.numero)))
    return para_revisao(db, [(folha, achadas)])


def para_revisao(db: Session, pares: list[tuple[Folha, list[Linha]]]) -> list[dict]:
    """As linhas como a tela de revisão as mostra, de uma folha ou de várias (a quarentena
    do grupo, D24), na ordem dada."""
    # Vizinhos a um dígito, em UMA consulta para todas as linhas. É a medida de
    # perigo de A15: um erro de leitura transforma a matrícula de alguém na de
    # outra pessoa real, o score sai alto, a margem sai folgada, e nem θ nem δ
    # percebem. 62,6% da base tem ao menos um vizinho assim.
    # Na base com que CADA folha foi lida (22/11): o vizinho perigoso é uma pessoa real
    # daquele contradomínio, e a lista do grupo pode trazer gente que a geral não tem (A66).
    candidatas: set[str] = set()
    ids_de = {folha.id: bases.ids_da_folha(db, folha) for folha, _ in pares}
    for _, achadas in pares:
        for linha in achadas:
            candidatas |= _variacoes(linha.matricula_sugerida)
    reais_por_base: dict[int | None, set[str]] = {}
    if candidatas:
        for base_id, matricula in db.execute(
                select(PessoaDaBase.base_id, PessoaDaBase.matricula)
                .where(PessoaDaBase.base_id.in_({b for ids in ids_de.values() for b in ids}),
                       PessoaDaBase.matricula.in_(candidatas))):
            reais_por_base.setdefault(base_id, set()).add(matricula)

    saida = []
    for folha, achadas in pares:
        reais = set().union(*(reais_por_base.get(i, set()) for i in ids_de[folha.id]))
        lista = bases.lista_da_folha(db, folha)
        # θ e δ vêm da FOLHA, não do `fuzzy`: são os que produziram estas decisões.
        # Explicar a rejeição com o limiar de hoje quando a leitura foi feita com o
        # de ontem daria uma explicação que não corresponde ao que aconteceu. O
        # `fuzzy` só entra como reserva, e importado aqui dentro — `experimento-fuzzy`
        # só existe no `sys.path` depois que o pipeline foi preparado.
        if folha.theta is None or folha.delta is None:
            pipeline.preparar()
            from fuzzy import THETA, DELTA                            # noqa: E402
        theta = folha.theta if folha.theta is not None else THETA
        delta = folha.delta if folha.delta is not None else DELTA
        # o τ é o da folha, nulo se ela foi lida antes da regra dos dois canais (D23)
        for l in achadas:
            dados = montar_linha(l, sorted(_variacoes(l.matricula_sugerida) & reais),
                                 theta, delta, folha.tau_nome)
            if lista is not None:
                # A66: quem revisa vê quais candidatos estão na lista do grupo
                dados["candidatos"] = [{**c, "na_lista": c.get("matricula") in lista}
                                       for c in (dados.get("candidatos") or [])]
            saida.append(dados)
    return saida


def _resumir(linhas: list[Linha]) -> ResumoRevisao:
    """O que a folha virou. Uma passada, sem consulta nova."""
    identificados = nao_ident = fora = anuladas = pendentes = duplicadas = 0
    for linha in linhas:
        ultima = ultima_revisao(linha)
        if linha.decisao == "anulada":
            anuladas += 1
        if linha.duplicada_na_folha:
            duplicadas += 1

        if ultima is not None:
            # A revisão humana MANDA sobre o gate: é para isso que ela existe.
            if ultima.matricula_escolhida:
                identificados += 1
            elif ultima.matricula_informada:               # D32
                fora += 1
            else:
                nao_ident += 1
        elif linha.decisao == "aceito":
            # Aceite não auditado conta como identificado pelo sistema — a
            # auditoria é por amostragem por decisão de método (A21), não por
            # esquecimento.
            identificados += 1
        elif linha.decisao == "quarentena":
            pendentes += 1

    return ResumoRevisao(linhas=len(linhas), identificados=identificados,
                         nao_identificados=nao_ident, fora_da_base=fora,
                         anuladas=anuladas,
                         pendentes=pendentes, duplicadas=duplicadas)


@rotas.get("/{folha_id}/resumo", response_model=ResumoRevisao)
def resumo(folha_id: int,
           db: Session = Depends(dependencia_sessao)) -> ResumoRevisao:
    if db.get(Folha, folha_id) is None:
        raise HTTPException(404, "folha não encontrada")
    return _resumir(list(db.scalars(select(Linha)
                                    .where(Linha.folha_id == folha_id))))


@rotas.post("/{folha_id}/concluir", response_model=ResumoRevisao)
def concluir(folha_id: int, request: Request, revisor: str | None = None,
             db: Session = Depends(dependencia_sessao)) -> ResumoRevisao:
    """Fecha a revisão da folha. Ato explícito, e não consequência.

    É este carimbo que separa "ninguém abriu esta folha ainda" de "esta folha
    foi conferida" — as duas coisas têm zero pendências e significam o oposto.
    A partir dele a frequência da folha vale.

    **Quarentena sem decisão impede concluir.** Aceite sem auditar não impede:
    a auditoria é por amostragem por decisão de método, mas a quarentena existe
    justamente porque o sistema não soube — deixá-la em aberto e carimbar a
    folha como revisada seria perder a linha em silêncio.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.estado != "processada":
        raise HTTPException(409, f"a folha está '{folha.estado}', não processada")

    linhas = list(db.scalars(select(Linha).where(Linha.folha_id == folha_id)))
    resultado = _resumir(linhas)
    if resultado.pendentes:
        raise HTTPException(
            409, f"{resultado.pendentes} linha(s) em quarentena sem decisão")

    folha.revisada_em = datetime.now(timezone.utc)
    folha.revisada_por = usuario_logado(request) or revisor      # ver `linhas.revisar`
    db.commit()
    return resultado


@rotas.post("/{folha_id}/reabrir", response_model=FolhaSaida)
def reabrir(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    """Tira o carimbo de concluída para a folha voltar a ser editável.

    **Nada é apagado.** A tabela de revisão é append-only, então as decisões
    anteriores continuam lá e a nova decisão empilha por cima. É isso que
    permite responder depois "quem mudou esta presença, e quando" — a pergunta
    do primeiro contencioso, e a razão de a rubrica existir na folha.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    folha.revisada_em = None
    folha.revisada_por = None
    db.commit()
    return montar_folha(folha, *_contadores(db, [folha_id]).get(folha_id, (0, 0)))


@rotas.post("/{folha_id}/usar-copia", response_model=FolhaSaida)
def usar_copia(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    """Esta cópia passa a valer, e a que valia vira cópia dela (D30).

    Nada é apagado: as decisões tomadas na outra ficam lá, só deixam de contar. Por isso a tela
    confirma antes, quando a outra já tem trabalho de gente.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.copia_de is None:
        raise HTTPException(409, "esta folha já é a que conta")
    reenvio.usar(db, folha)
    db.commit()
    return obter(folha_id, db)


@rotas.post("/{folha_id}/nao-e-copia", response_model=FolhaSaida)
def nao_e_copia(folha_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    """Quem revisa diz que a detecção errou: são papéis diferentes, e os dois contam (D30).

    A marca fica na folha, e a detecção não a remarca, nem quando ela é relida.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.copia_de is None:
        raise HTTPException(409, "esta folha não está marcada como cópia")
    folha.copia_de = folha.semelhanca_copia = None
    folha.nao_e_copia = True
    db.commit()
    return obter(folha_id, db)


@rotas.post("/reler-erros")
def reler_erros(tarefas: BackgroundTasks, grupo: str | None = None,
                db: Session = Depends(dependencia_sessao)) -> dict:
    """Devolve à fila as folhas em erro (do grupo, ou todas). Um lote inteiro em erro eram 62
    cliques em "reavaliar" (A63). Só `erro`: a "não é folha" pede outro escaneamento, e a
    "suspeita" costuma ser geometria, que se escolhe folha a folha. Folha em erro não tem
    linha, então não há decisão humana a perder.
    """
    if grupo is not None:
        candidatas = bases.folhas_do_grupo(db, grupo)
    else:
        candidatas = list(db.scalars(select(Folha).where(Folha.estado == "erro")))
    em_erro = [f for f in candidatas if f.estado == "erro"]
    for folha in em_erro:
        _zerar_leitura(folha, db)
    db.commit()
    if em_erro:
        tarefas.add_task(ler_fila)
    return {"relidas": len(em_erro)}


@rotas.post("/{folha_id}/reprocessar", response_model=FolhaSaida)
def reprocessar(folha_id: int, tarefas: BackgroundTasks,
                modelo_folha: str | None = None, forcar: bool = False,
                db: Session = Depends(dependencia_sessao)) -> dict:
    """Lê a folha de novo, opcionalmente com outra geometria.

    Existe por causa de um modo de falha real: recortar com o modelo errado não
    dá erro, dá lixo com cara de leitura (ver o portão em `_ler_e_gravar`).
    Sem reprocessar, a única saída seria apagar e reenviar — perdendo o que
    já foi revisado nas folhas que estavam certas.

    **Descarta as linhas, e com elas as revisões.** Por isso `forcar` é exigido
    quando já existe decisão humana: reler é jogar fora trabalho de gente, e
    isso não pode acontecer por engano. A folha em si e a imagem ficam.
    """
    folha = _folha_para_reler(db, folha_id, forcar)
    if modelo_folha:
        folha.modelo_folha = modelo_folha
    _zerar_leitura(folha, db)
    db.commit()

    tarefas.add_task(ler_fila)
    return montar_folha(folha)


def _folha_para_reler(db: Session, folha_id: int, forcar: bool) -> Folha:
    """A folha, se puder ser lida de novo. As travas de `reprocessar` e de `substituir`.

    O "não identificado" automático (D26) não é decisão humana: a releitura o refaz.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    if folha.estado == "processando":
        raise HTTPException(409, "esta folha está sendo lida agora")
    revisadas = db.scalar(
        select(func.count()).select_from(Revisao)
        .join(Linha, Revisao.linha_id == Linha.id)
        .where(Linha.folha_id == folha_id,
               or_(Revisao.revisor.is_(None),
                   Revisao.revisor != REVISOR_AUTOMATICO))) or 0
    if revisadas and not forcar:
        raise HTTPException(
            409, f"{revisadas} decisão(ões) humana(s) seriam descartadas; "
                 "repita com forcar=true se for mesmo para reler")
    return folha


def _zerar_leitura(folha: Folha, db: Session) -> None:
    """Descarta as linhas (e com elas as revisões) e devolve a folha à fila."""
    reenvio.soltar(db, folha)                 # D30: a comparação é refeita depois da leitura
    for linha in list(folha.linhas):
        db.delete(linha)                      # revisões saem por cascade
    folha.estado = "recebida"
    folha.na_fila_desde = datetime.now(timezone.utc)
    folha.erro = None
    folha.linhas_preenchidas = folha.linhas_anuladas = 0
    folha.processada_em = folha.revisada_em = None
    folha.revisada_por = None


@rotas.post("/{folha_id}/substituir", response_model=FolhaSaida)
async def substituir(folha_id: int, tarefas: BackgroundTasks,
                     arquivo: UploadFile = File(...), forcar: bool = Form(False),
                     db: Session = Depends(dependencia_sessao)) -> dict:
    """Troca a imagem por outro escaneamento do MESMO papel e lê de novo (A61).

    O caso que a motivou: a folha que entrou torta no alimentador e perdeu um marcador
    ("não é folha de presença"), ou que saiu com um vinco atravessando o marcador. Sem
    isto, a saída era apagar e reenviar, e o reenvio criava uma folha nova, sem o rótulo do
    lote, com o risco de a mesma folha existir duas vezes e contar todo mundo em dobro.

    Aceita uma imagem ou um PDF de UMA página: o pedido é "esta folha", e um PDF com
    várias páginas é quase sempre o lote inteiro enviado no lugar errado.
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "arquivo vazio")
    folha = _folha_para_reler(db, folha_id, forcar)

    origem = documentos.sha(conteudo)
    if origem == folha.origem_sha256:
        raise HTTPException(409, "é o mesmo arquivo que esta folha já tem")
    outra = db.scalars(select(Folha).where(Folha.origem_sha256 == origem)).first()
    if outra is not None:
        raise HTTPException(
            409, f"este arquivo já está no sistema como a folha {outra.id} "
                 f"({outra.nome_original}, página {outra.pagina})")

    # gera numa pasta temporária: só depois de saber que é uma página só o arquivo vai
    # para a pasta das folhas
    with tempfile.TemporaryDirectory() as tmp:
        try:
            # numa thread, pelo mesmo motivo do `enviar`
            imagens = await run_in_threadpool(documentos.paginas, conteudo, Path(tmp), origem)
        except RuntimeError as e:
            raise HTTPException(422, str(e)) from e
        if len(imagens) != 1:
            raise HTTPException(
                422, f"o PDF tem {len(imagens)} páginas; envie só o escaneamento desta "
                     "folha (uma página)")
        config.pasta_folhas.mkdir(parents=True, exist_ok=True)
        nova = config.pasta_folhas / imagens[0].name
        shutil.move(str(imagens[0]), nova)

    antiga = Path(folha.arquivo)
    folha.arquivo = str(nova)
    folha.origem_sha256 = origem
    folha.sha256 = documentos.sha(nova.read_bytes())
    folha.reescaneada_em = datetime.now(timezone.utc)
    folha.arquivo_reescaneado = (arquivo.filename or "sem nome")[:300]
    _zerar_leitura(folha, db)
    try:
        db.commit()
    except Exception:
        nova.unlink(missing_ok=True)          # o banco não mudou: o arquivo novo sai
        raise

    # a imagem antiga sai DEPOIS do commit, como em `apagar_folhas`: se o banco falhar,
    # o registro continua apontando para uma imagem que existe
    shutil.rmtree(pipeline.pasta_recortes(antiga), ignore_errors=True)
    if antiga != nova:
        antiga.unlink(missing_ok=True)

    tarefas.add_task(ler_fila)
    return montar_folha(folha)


@rotas.get("/{folha_id}/json")
def baixar_json(folha_id: int, db: Session = Depends(dependencia_sessao)):
    """O registro completo da folha, para baixar.

    É o entregável do sistema (objetivo específico i): a frequência apurada com
    **como ela foi apurada junto** — ponto de operação, modelos que leram cada
    campo, leitura crua, candidatos ranqueados e a decisão humana de cada linha.

    Sem a procedência, o arquivo seria uma lista de presença sem como conferir;
    com ela, qualquer número dele é reproduzível.
    """
    folha = db.get(Folha, folha_id)
    if folha is None:
        raise HTTPException(404, "folha não encontrada")
    dados = registro_da_folha(db, folha)
    nome = f"folha_{folha.id}_{Path(folha.nome_original).stem}.json"
    return JSONResponse(
        dados,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'})


def registro_da_folha(db: Session, folha: Folha) -> dict:
    """O registro completo de uma folha, com a procedência — o do download por folha e o de
    cada folha no JSON do grupo (D24), que assim não podem divergir."""
    linhas = list(db.scalars(select(Linha).where(Linha.folha_id == folha.id)
                             .order_by(Linha.numero)))
    dados = {
        "folha": {
            "id": folha.id, "arquivo": folha.nome_original,
            "pagina": folha.pagina, "modelo_folha": folha.modelo_folha,
            "estado": folha.estado,
            "processada_em": folha.processada_em.isoformat()
            if folha.processada_em else None,
            "revisada_em": folha.revisada_em.isoformat()
            if folha.revisada_em else None,
            # D29: a folha foi lida com o canto deste marcador estimado pelos outros três
            "marcador_estimado": folha.marcador_estimado,
            # D30: se esta folha é outro escaneamento de uma que conta, qual é ela
            "copia_de": folha.copia_de,
            "semelhanca_copia": folha.semelhanca_copia,
            "revisada_por": folha.revisada_por,
        },
        "ponto_de_operacao": {
            "alpha": folha.alpha, "theta": folha.theta, "delta": folha.delta,
            "metrica_nome": folha.metrica_nome,
            "tau_nome": folha.tau_nome, "recorte": folha.recorte,
            "corte_acaso": folha.corte_acaso,
            "modelo_matricula": folha.modelo_matricula,
            "modelo_letras": folha.modelo_letras,
        },
        "linhas": [],
    }
    for linha in linhas:
        ultima = ultima_revisao(linha)
        dados["linhas"].append({
            "numero": linha.numero,
            "leitura_matricula": linha.leitura_matricula,
            "leitura_nome": linha.leitura_nome,
            "score": linha.score, "margem": linha.margem,
            "decisao": linha.decisao,
            "matricula_sugerida": linha.matricula_sugerida,
            "candidatos": linha.candidatos,
            "duplicada_na_folha": linha.duplicada_na_folha,
            "revisao": None if ultima is None else {
                "matricula_escolhida": ultima.matricula_escolhida,
                "matricula_informada": ultima.matricula_informada,
                "nome_informado": ultima.nome_informado,
                "revisor": ultima.revisor, "ms": ultima.ms,
                "em": ultima.criado_em.isoformat(),
            },
        })
    return dados


def _variacoes(matricula: str | None) -> set[str]:
    """Toda matrícula a UMA substituição de dígito da dada."""
    if not matricula:
        return set()
    return {matricula[:i] + d + matricula[i + 1:]
            for i in range(len(matricula)) for d in "0123456789"
            if d != matricula[i]}
