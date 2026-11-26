"""As bases de pessoas: a geral do sistema e a lista de cada grupo (pedido do autor em 22/11/2026).

Decisões do autor, de 22/11:
  - a lista do grupo NÃO substitui a geral (A66): a identificação é contra a geral e a lista
    filtra quem sai aceito. A primeira versão, do mesmo dia, substituía, e o Experimento 04
    mediu 3–5% de quem estava fora da lista virando outra pessoa;
  - trocar ou apagar a base de um grupo com folhas lidas avisa e oferece reler, e só relê
    com confirmação, porque reler descarta decisões humanas;
  - apagar tira de uso, mas a base fica no banco enquanto alguma folha apontar para ela (a
    frequência antiga não perde nomes); sem folha nenhuma, é apagada de vez;
  - toda base nova passa por uma prévia: quantos entram, saem e mudam, e alertas de lista
    velha. Em 20/11 a lista enviada era de abril: 375 sairiam, entre eles todos os calouros.

O `.xlsx` do repositório continua sendo a base dos experimentos e não é tocado; aqui ele só
entra uma vez, como semente da base geral, para as folhas já lidas terem a quem apontar.
"""
from __future__ import annotations

import csv
import hashlib
import io
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import config
from .tabelas import BaseDePessoas, Evento, Folha, PessoaDaBase

# D25: quem está FORMANDO ou GRADUANDO ainda está matriculado. O CSV do SIGAA traz só esses
# três; qualquer outro status (trancado, cancelado) fica de fora e a prévia diz quantos.
STATUS_MATRICULADO = {"ATIVO", "FORMANDO", "GRADUANDO"}
# A prévia alerta quando sai mais que isto da base atual. A lista de abril tirava 28%.
SAIDA_ALTA = 0.10
LIMITE_BYTES = 20 * 1024 * 1024


def _maiusculas(texto: str) -> str:
    """A–Z + espaço, sem acento (D05). Igual a `normalizacao.normalizar`, mas sem depender do
    `sys.path` do pipeline: a prévia roda antes de qualquer leitura."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto)
                         if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if "A" <= c <= "Z" else " "
                            for c in sem_acento.upper()).split())


def _campo(cabecalho: str) -> str | None:
    chave = _maiusculas(cabecalho).replace(" ", "")
    return {"MATRICULA": "matricula", "NOME": "nome", "NOMECOMPLETO": "nome",
            "CURSO": "curso", "STATUS": "status", "SITUACAO": "status"}.get(chave)


@dataclass
class Lida:
    """O que saiu do arquivo, antes de comparar com o banco."""
    pessoas: list[dict]
    aba: str | None = None
    data_aba: date | None = None
    fora_por_status: dict[str, int] = field(default_factory=dict)
    sem_matricula_ou_nome: int = 0
    matricula_invalida: int = 0
    repetidas: int = 0


def _matricula(bruta) -> str | None:
    """float → int → str (o Excel entrega 559707.0). Uma de 5 dígitos fica como está."""
    if bruta is None:
        return None
    if isinstance(bruta, float):
        if not bruta.is_integer():
            return None
        bruta = int(bruta)
    texto = str(bruta).strip()
    return texto if texto.isascii() and texto.isdigit() else None


def _linhas_xlsx(conteudo: bytes) -> tuple[list[tuple], str, date | None]:
    sys.path.insert(0, str(config.pasta_pipeline.parent / "experimento-fuzzy"))
    from base_alunos import _data_da_aba, escolher_aba            # noqa: E402
    wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    aba = escolher_aba(wb)
    return list(wb[aba].iter_rows(values_only=True)), aba, _data_da_aba(aba)


def _linhas_csv(conteudo: bytes) -> list[list[str]]:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")
    try:
        dialeto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    return [linha for linha in csv.reader(io.StringIO(texto), dialeto) if any(linha)]


def ler_arquivo(nome_arquivo: str, conteudo: bytes) -> Lida:
    """Lê um `.xlsx` ou `.csv` com as colunas Matrícula e Nome (Curso e Status, se houver)."""
    if not conteudo:
        raise ValueError("arquivo vazio")
    if len(conteudo) > LIMITE_BYTES:
        raise ValueError("arquivo grande demais para uma lista de pessoas")
    aba = data_aba = None
    nome = nome_arquivo.lower()
    if nome.endswith(".xlsx"):
        linhas, aba, data_aba = _linhas_xlsx(conteudo)
    elif nome.endswith(".csv"):
        linhas = _linhas_csv(conteudo)
    else:
        raise ValueError("envie um .xlsx ou um .csv")
    if not linhas:
        raise ValueError("a planilha não tem linhas")

    col = {}
    for i, celula in enumerate(linhas[0]):
        campo = _campo(str(celula)) if celula is not None else None
        if campo and campo not in col:
            col[campo] = i
    faltam = {"matricula", "nome"} - set(col)
    if faltam:
        raise ValueError("a primeira linha precisa ter as colunas Matrícula e Nome; "
                         f"faltou {', '.join(sorted(faltam))}")

    def celula(linha, campo):
        i = col.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    lida = Lida(pessoas=[], aba=aba, data_aba=data_aba)
    vistas, fora = set(), Counter()
    for linha in linhas[1:]:
        bruta, nome_pessoa = celula(linha, "matricula"), celula(linha, "nome")
        if bruta in (None, "") or nome_pessoa in (None, "") or not str(nome_pessoa).strip():
            lida.sem_matricula_ou_nome += 1
            continue
        status = celula(linha, "status")
        if "status" in col and _maiusculas(str(status or "")) not in STATUS_MATRICULADO:
            fora[_maiusculas(str(status or "")) or "SEM STATUS"] += 1
            continue
        matricula = _matricula(bruta)
        if matricula is None:
            lida.matricula_invalida += 1
            continue
        if matricula in vistas:
            lida.repetidas += 1
            continue
        vistas.add(matricula)
        curso = celula(linha, "curso")
        lida.pessoas.append({"matricula": matricula, "nome": " ".join(str(nome_pessoa).split()),
                             "curso": str(curso).strip() if curso not in (None, "") else None})
    lida.fora_por_status = dict(fora)
    if not lida.pessoas:
        raise ValueError("nenhuma pessoa com matrícula e nome na planilha")
    return lida


# --- qual base vale ---------------------------------------------------------------------

def ativa(db: Session, grupo: str | None) -> BaseDePessoas | None:
    """A base ativa DESTE escopo: a do grupo, ou a geral com `grupo=None`."""
    return db.scalar(select(BaseDePessoas).where(
        BaseDePessoas.ativa.is_(True),
        BaseDePessoas.grupo.is_(None) if grupo is None else BaseDePessoas.grupo == grupo)
        .order_by(BaseDePessoas.id.desc()).limit(1))


def para_identificar(db: Session, grupo: str | None
                     ) -> tuple[BaseDePessoas | None, BaseDePessoas | None]:
    """(base, lista) com que uma folha do grupo é lida.

    Desde 22/11 (A66, Experimento 04 parte 2), a lista do grupo **não substitui** a geral: a
    identificação é contra a geral (mais quem só está na lista), para o δ ter os concorrentes
    do campus, e só sai aceito quem está na lista. Com a lista substituindo, quem assinava e não
    estava nela virava outra pessoa em 3–5% das vezes; assim, 0 quando a pessoa é do campus.
    Sem base geral, a lista é a base (não há concorrência a preservar).
    """
    geral = ativa(db, None)
    lista = ativa(db, grupo) if grupo else None
    if geral is None:
        return lista, None
    return geral, lista


def efetiva(db: Session, grupo: str | None) -> BaseDePessoas | None:
    """A base de identificação de uma folha do grupo (ver `para_identificar`)."""
    return para_identificar(db, grupo)[0]


def grupo_da_folha(db: Session, folha: Folha) -> str | None:
    if folha.evento_id is not None:
        evento = db.get(Evento, folha.evento_id)
        if evento is not None and evento.grupo:
            return evento.grupo
    return folha.grupo_envio


def base_da_folha(db: Session, folha: Folha) -> int | None:
    """A base contra a qual decisões sobre esta folha se conferem: a com que ela foi lida.

    Folha sem `base_id` só existe se foi lida antes das bases e a semente ainda não rodou;
    nesse caso, a que valeria hoje.
    """
    if folha.base_id is not None:
        return folha.base_id
    base = efetiva(db, grupo_da_folha(db, folha))
    return base.id if base else None


def ids_da_folha(db: Session, folha: Folha) -> list[int]:
    """As bases em que uma pessoa desta folha pode estar: a de identificação e a lista."""
    return [i for i in (base_da_folha(db, folha), folha.lista_id) if i is not None]


_MATRICULAS: dict[int, frozenset[str]] = {}


def matriculas(db: Session, base_id: int) -> frozenset[str]:
    """As matrículas de uma base. Cacheado: base não muda depois de criada."""
    if base_id not in _MATRICULAS:
        _MATRICULAS[base_id] = frozenset(db.scalars(
            select(PessoaDaBase.matricula).where(PessoaDaBase.base_id == base_id)))
    return _MATRICULAS[base_id]


def lista_da_folha(db: Session, folha: Folha) -> frozenset[str] | None:
    """Quem pode ser aceito nesta folha, se ela foi lida com lista de grupo; senão None."""
    return matriculas(db, folha.lista_id) if folha.lista_id is not None else None


def pessoa(db: Session, ids: list[int] | int | None, matricula: str) -> PessoaDaBase | None:
    """A pessoa na primeira das bases em que estiver (a de identificação antes da lista)."""
    for base_id in ([ids] if isinstance(ids, int) else ids or []):
        achada = db.get(PessoaDaBase, (base_id, matricula))
        if achada is not None:
            return achada
    return None


# --- prévia e aplicação ---------------------------------------------------------------------

def _sha(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def previa(db: Session, lida: Lida, conteudo: bytes, grupo: str | None,
           data_referencia: date | None) -> dict:
    """Compara com a anterior do mesmo escopo: a geral com a geral, a lista com a lista do grupo.

    A lista não substitui mais a geral (22/11), então compará-la com a geral não diz nada; o
    que importa dela é quantas pessoas a geral já conhece (as outras só a lista identifica).
    """
    atual = ativa(db, grupo)
    geral = ativa(db, None) if grupo else None
    data = data_referencia or lida.data_aba
    novas = {p["matricula"]: p for p in lida.pessoas}
    antigas = ({p.matricula: p for p in db.scalars(
        select(PessoaDaBase).where(PessoaDaBase.base_id == atual.id))} if atual else {})
    entram = [m for m in novas if m not in antigas]
    saem = [m for m in antigas if m not in novas]
    mudam = [m for m in novas if m in antigas
             and (novas[m]["nome"] != antigas[m].nome or novas[m]["curso"] != antigas[m].curso)]

    alertas = []
    if atual and atual.sha256 == _sha(conteudo):
        alertas.append("é o mesmo arquivo da base que está valendo")
    if antigas and len(saem) / len(antigas) > SAIDA_ALTA:
        alertas.append(f"saem {len(saem)} das {len(antigas)} pessoas da base atual "
                       f"({100 * len(saem) / len(antigas):.0f}%). Uma lista velha faz isso: "
                       "confira a data da extração")
    if data and atual and atual.data_referencia and data < atual.data_referencia:
        alertas.append(f"a lista é de {data:%d/%m/%Y}, mais velha que a atual "
                       f"({atual.data_referencia:%d/%m/%Y})")
    if not data and not grupo:
        alertas.append("sem a data da extração: informe-a, para a próxima prévia poder "
                       "avisar de lista velha")
    tamanhos = Counter(len(m) for m in novas)
    estranhas = sum(n for t, n in tamanhos.items() if t not in (5, 6))
    if estranhas:
        alertas.append(f"{estranhas} matrícula(s) sem 5 ou 6 dígitos")
    return {
        "grupo": grupo, "arquivo": None, "aba": lida.aba,
        "data_referencia": data.isoformat() if data else None,
        "pessoas": len(lida.pessoas), "entram": len(entram), "saem": len(saem),
        "mudam": len(mudam),
        "comparada_com": resumo(db, atual) if atual else None,
        "fora_por_status": lida.fora_por_status,
        "sem_matricula_ou_nome": lida.sem_matricula_ou_nome,
        "matricula_invalida": lida.matricula_invalida, "repetidas": lida.repetidas,
        "alertas": alertas,
        "na_geral": (len(set(novas) & matriculas(db, geral.id)) if geral else None),
    }


def _folhas_que_usam(db: Session, base_id: int) -> int:
    return db.scalar(select(func.count(Folha.id)).where(
        or_(Folha.base_id == base_id, Folha.lista_id == base_id))) or 0


def retirar(db: Session, base: BaseDePessoas) -> str:
    """Tira de uso. Com folhas apontando, fica no histórico; sem nenhuma, sai de vez."""
    if _folhas_que_usam(db, base.id):
        base.ativa = False
        base.retirada_em = datetime.now(timezone.utc)
        return "guardada"
    db.query(PessoaDaBase).filter(PessoaDaBase.base_id == base.id).delete()
    db.delete(base)
    return "apagada"


def limpar_retiradas(db: Session) -> int:
    """Apaga de vez as bases fora de uso que nenhuma folha usa mais.

    Uma base retirada fica guardada pelas folhas lidas com ela; relidas essas folhas com outra
    base, ela não guarda mais nada, e é dado pessoal sem motivo para ficar (22/11: a lista de
    abril continuava no banco depois de o grupo voltar à geral e reler tudo).
    """
    sobras = list(db.scalars(select(BaseDePessoas).where(
        BaseDePessoas.ativa.is_(False),
        ~select(Folha.id).where(or_(Folha.base_id == BaseDePessoas.id,
                                    Folha.lista_id == BaseDePessoas.id)).exists())))
    for base in sobras:
        db.query(PessoaDaBase).filter(PessoaDaBase.base_id == base.id).delete()
        db.delete(base)
        _MATRICULAS.pop(base.id, None)
        for chave in [k for k in _PARA_LEITURA if base.id in k]:
            _PARA_LEITURA.pop(chave)
    return len(sobras)


def aplicar(db: Session, lida: Lida, nome_arquivo: str, conteudo: bytes, grupo: str | None,
            data_referencia: date | None, quem: str | None,
            origem: str = "envio") -> BaseDePessoas:
    anterior = ativa(db, grupo)
    nova = BaseDePessoas(grupo=grupo, arquivo_original=nome_arquivo[:300], sha256=_sha(conteudo),
                         aba=lida.aba, data_referencia=data_referencia or lida.data_aba,
                         origem=origem, pessoas=len(lida.pessoas), enviada_por=quem,
                         ativa=True)
    db.add(nova)
    db.flush()
    db.add_all(PessoaDaBase(base_id=nova.id, matricula=p["matricula"], nome=p["nome"],
                            nome_normalizado=_maiusculas(p["nome"]), curso=p["curso"])
               for p in lida.pessoas)
    if anterior is not None:
        retirar(db, anterior)
    db.flush()
    return nova


def resumo(db: Session, base: BaseDePessoas) -> dict:
    return {"id": base.id, "grupo": base.grupo, "arquivo": base.arquivo_original,
            "aba": base.aba, "origem": base.origem, "pessoas": base.pessoas,
            "data_referencia": base.data_referencia.isoformat() if base.data_referencia else None,
            "enviada_em": base.enviada_em.isoformat(timespec="minutes")
            if base.enviada_em else None,
            "enviada_por": base.enviada_por, "folhas": _folhas_que_usam(db, base.id)}


def planilha(db: Session, base: BaseDePessoas) -> bytes:
    """A base como `.xlsx`, no formato que o próprio envio lê: baixada, editada e reenviada,
    ela volta igual. A aba leva a data da extração (é de lá que o envio a tira); sem data, um
    nome qualquer. Sai do banco, e não do arquivo enviado, porque o arquivo não é guardado."""
    livro = Workbook()
    aba = livro.active
    aba.title = f"{base.data_referencia:%d.%m.%Y}" if base.data_referencia else "pessoas"
    aba.append(["Matrícula", "Nome", "Curso"])
    for celula in aba[1]:
        celula.font = Font(bold=True)
    for n, p in enumerate(db.scalars(select(PessoaDaBase).where(
            PessoaDaBase.base_id == base.id).order_by(PessoaDaBase.nome_normalizado,
                                                      PessoaDaBase.matricula)), start=2):
        for coluna, valor in enumerate((p.matricula, p.nome, p.curso), start=1):
            if valor is not None:
                # texto sempre: um "=..." na lista enviada não pode virar fórmula na baixada
                celula = aba.cell(row=n, column=coluna, value=valor)
                celula.data_type = "s"
    aba.freeze_panes = "A2"
    aba.auto_filter.ref = aba.dimensions
    for coluna, largura in zip("ABC", (12, 48, 40)):
        aba.column_dimensions[coluna].width = largura
    corpo = io.BytesIO()
    livro.save(corpo)
    return corpo.getvalue()


# --- o que ficou lido com outra base ------------------------------------------------------

def folhas_do_grupo(db: Session, grupo: str) -> list[Folha]:
    """As folhas que pertencem ao grupo: pelo evento, ou enviadas por ele e ainda sem evento."""
    eventos = select(Evento.id).where(Evento.grupo == grupo)
    return list(db.scalars(select(Folha).where(or_(
        Folha.evento_id.in_(eventos),
        (Folha.evento_id.is_(None)) & (Folha.grupo_envio == grupo)))))


def desatualizadas(db: Session, grupo: str | None) -> list[Folha]:
    """Lidas com uma base diferente da que o escopo usa hoje.

    Com `grupo`, as folhas do grupo. Sem, todas, porque todas são identificadas contra a geral
    (A66). As cópias (D30) ENTRAM: relendo só a que valia, a
    cópia antiga, lida com a base maior, tinha mais aceites e voltava a valer (22/11, 60 dos 62
    papéis). Relido o papel inteiro, a comparação das cópias é entre leituras da mesma base.
    """
    if grupo is not None:
        candidatas = folhas_do_grupo(db, grupo)
    else:
        # todas: a geral é a base de identificação de todo grupo (22/11)
        candidatas = list(db.scalars(select(Folha)))
    saida = []
    for f in candidatas:
        if f.estado not in ("processada", "suspeita"):
            continue
        base, lista = para_identificar(db, grupo_da_folha(db, f))
        alvo = (base.id if base else None, lista.id if lista else None)
        if (f.base_id, f.lista_id) != alvo:
            saida.append(f)
    return saida


# --- a leitura ------------------------------------------------------------------------------

_PARA_LEITURA: dict[tuple[int, int | None], list[dict]] = {}


def para_leitura(db: Session, base_id: int, lista_id: int | None = None) -> list[dict]:
    """A base no formato do pipeline, com o nome já normalizado: a de identificação, mais
    quem só está na lista (um convidado de fora do campus só seria aceito se a identificação
    o conhecesse). Cacheada pelo par de ids: base não muda depois de criada."""
    chave = (base_id, lista_id)
    if chave not in _PARA_LEITURA:
        def pessoas(i):
            return [{"matricula": m, "nome": n, "curso": c} for m, n, c in db.execute(
                select(PessoaDaBase.matricula, PessoaDaBase.nome_normalizado,
                       PessoaDaBase.curso).where(PessoaDaBase.base_id == i))]
        juntas = {p["matricula"]: p for p in (pessoas(lista_id) if lista_id else [])}
        juntas.update({p["matricula"]: p for p in pessoas(base_id)})    # a geral prevalece
        _PARA_LEITURA[chave] = [juntas[m] for m in sorted(juntas)]
    return _PARA_LEITURA[chave]


# --- a semente ------------------------------------------------------------------------------

def semear(db: Session) -> BaseDePessoas | None:
    """Na primeira subida sem base nenhuma: a planilha do repositório vira a base geral, e as
    folhas já lidas (todas com ela, D07) passam a apontar para essa base.

    Sem isto, uma instalação nova recusava toda escolha de candidato na revisão (A63).
    """
    if db.scalar(select(BaseDePessoas.id).limit(1)) is not None:
        return None
    from .pipeline import preparar
    preparar()
    from identificar_folha import BASE_XLSX                      # noqa: E402
    from base_alunos import _data_da_aba, carregar_base, escolher_aba  # noqa: E402
    aba = escolher_aba(load_workbook(BASE_XLSX, read_only=True))
    pessoas = [{"matricula": p["matricula"], "nome": " ".join(str(p["nome"]).split()),
                "curso": p.get("curso")} for p in carregar_base(BASE_XLSX)]
    conteudo = Path(BASE_XLSX).read_bytes()
    nome = str(BASE_XLSX).rsplit("/", 1)[-1]
    base = aplicar(db, Lida(pessoas=pessoas, aba=aba, data_aba=_data_da_aba(aba)), nome,
                   conteudo, None, None, None, origem="semente")
    db.query(Folha).filter(Folha.base_id.is_(None)).update({Folha.base_id: base.id})
    db.commit()
    return base
