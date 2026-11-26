"""Enviar, ver e apagar as bases de pessoas: a geral e a de cada grupo (22/11). Ver `api/bases.py`.

Só admin: a conta de impressão (D18) não alcança nada disto, porque a lista é dado pessoal.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query,
                     Request, Response, UploadFile)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import bases
from ..db import dependencia_sessao
from ..esquemas import REVISOR_AUTOMATICO
from ..sessao import usuario_logado
from ..tabelas import BaseDePessoas, Grupo, Linha, Revisao
from .folhas import _zerar_leitura, ler_fila

rotas = APIRouter(prefix="/bases", tags=["bases"])


def _grupo(db: Session, grupo: str | None) -> str | None:
    grupo = (grupo or "").strip() or None
    if grupo is not None and db.scalar(select(Grupo.id).where(Grupo.nome == grupo)) is None:
        raise HTTPException(404, f"grupo {grupo!r} não existe")
    return grupo


def _data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise HTTPException(422, f"data da extração inválida: {texto!r} (use AAAA-MM-DD)")


def _decisoes_humanas(db: Session, folha_ids: list[int]) -> int:
    if not folha_ids:
        return 0
    return db.scalar(
        select(func.count()).select_from(Revisao).join(Linha, Revisao.linha_id == Linha.id)
        .where(Linha.folha_id.in_(folha_ids),
               or_(Revisao.revisor.is_(None), Revisao.revisor != REVISOR_AUTOMATICO))) or 0


def _situacao(db: Session, grupo: str | None) -> dict:
    """O que a tela mostra: a base do escopo, a que ele herda, e o que ficou para reler."""
    velhas = bases.desatualizadas(db, grupo)
    propria = bases.ativa(db, grupo)
    geral = bases.ativa(db, None)
    return {
        "grupo": grupo,
        "propria": bases.resumo(db, propria) if propria else None,
        "geral": bases.resumo(db, geral) if geral else None,
        "desatualizadas": len(velhas),
        "decisoes_humanas": _decisoes_humanas(db, [f.id for f in velhas]),
    }


@rotas.get("")
def situacao(grupo: str | None = None, db: Session = Depends(dependencia_sessao)) -> dict:
    return _situacao(db, _grupo(db, grupo))


async def _ler(arquivo: UploadFile) -> tuple[str, bytes, bases.Lida]:
    conteudo = await arquivo.read()
    nome = arquivo.filename or "lista"
    try:
        return nome, conteudo, bases.ler_arquivo(nome, conteudo)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@rotas.post("/previa")
async def previa(arquivo: UploadFile = File(...), grupo: str | None = Form(None),
                 data: str | None = Form(None),
                 db: Session = Depends(dependencia_sessao)) -> dict:
    """O que mudaria, sem gravar nada."""
    grupo = _grupo(db, grupo)
    nome, conteudo, lida = await _ler(arquivo)
    return {**bases.previa(db, lida, conteudo, grupo, _data(data)), "arquivo": nome}


@rotas.post("", status_code=201)
async def enviar(request: Request, arquivo: UploadFile = File(...),
                 grupo: str | None = Form(None), data: str | None = Form(None),
                 db: Session = Depends(dependencia_sessao)) -> dict:
    """Aplica: a base nova passa a valer no escopo, e a anterior sai de uso.

    O arquivo vem de novo, e não um id de prévia: a prévia não guarda nada, e o que se aplica
    é exatamente o arquivo que acabou de ser conferido.
    """
    grupo = _grupo(db, grupo)
    nome, conteudo, lida = await _ler(arquivo)
    bases.aplicar(db, lida, nome, conteudo, grupo, _data(data), usuario_logado(request))
    db.commit()
    return _situacao(db, grupo)


@rotas.get("/{base_id}/planilha.xlsx")
def baixar(base_id: int, db: Session = Depends(dependencia_sessao)) -> Response:
    """A lista que está valendo, em planilha (pedido do autor em 24/11)."""
    base = db.get(BaseDePessoas, base_id)
    if base is None or not base.ativa:
        raise HTTPException(404, "base não encontrada")
    nome = f"lista {base.grupo}" if base.grupo else "base geral"
    if base.data_referencia:
        nome += f" {base.data_referencia:%Y-%m-%d}"
    reserva = re.sub(r"[^A-Za-z0-9_-]+", "_", nome).strip("_")
    return Response(
        bases.planilha(db, base),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{reserva}.xlsx"; '
                                        f"filename*=UTF-8''{quote(nome + '.xlsx', safe='')}",
                 # dado pessoal: o navegador não guarda cópia
                 "Cache-Control": "no-store"})


@rotas.delete("/{base_id}")
def apagar(base_id: int, db: Session = Depends(dependencia_sessao)) -> dict:
    """Tira de uso. Usada por alguma folha, fica guardada para a frequência antiga; sem uso,
    sai de vez. Um grupo sem base volta a usar a geral."""
    base = db.get(BaseDePessoas, base_id)
    if base is None or not base.ativa:
        raise HTTPException(404, "base não encontrada")
    grupo = base.grupo
    resultado = bases.retirar(db, base)
    db.commit()
    return {**_situacao(db, grupo), "resultado": resultado}


@rotas.post("/reler")
def reler(tarefas: BackgroundTasks, grupo: str | None = Query(None), forcar: bool = False,
          db: Session = Depends(dependencia_sessao)) -> dict:
    """Relê as folhas lidas com outra base (decisão do autor em 22/11: avisar e oferecer, e só
    com confirmação). Reler descarta as linhas e as decisões humanas delas, então `forcar` é
    exigido quando há alguma — a mesma trava de "reavaliar"."""
    grupo = _grupo(db, grupo)
    velhas = bases.desatualizadas(db, grupo)
    humanas = _decisoes_humanas(db, [f.id for f in velhas])
    if humanas and not forcar:
        raise HTTPException(409, f"{humanas} decisão(ões) humana(s) seriam descartadas; "
                                 "confirme para reler mesmo assim")
    for folha in velhas:
        _zerar_leitura(folha, db)
    db.commit()
    if velhas:
        tarefas.add_task(ler_fila)            # o leitor único, como no envio e no "reavaliar"
    return {**_situacao(db, grupo), "relidas": len(velhas)}
