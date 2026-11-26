"""A busca no contradomínio: quem pode ser identificado (D07).

A busca livre existe para o caso que a A39 mediu custando 15,8 s: quando a
pessoa certa **não está** entre os candidatos ranqueados, o revisor precisa
procurá-la na base. Foi o que aconteceu na m7_02-05, com a matrícula lida com
cinco dígitos errados.

Desde 22/11 a base não é uma só: a busca procura na base com que a folha daquela
linha foi lida e na lista do grupo (A66). Sem linha, nas que o grupo usa; sem grupo,
na geral. Enviar e apagar bases é `rotas/bases.py`.

A busca é sobre o nome normalizado (D05: A–Z e espaço, acento removido por NFD)
porque muitos nomes da base têm acento e ninguém digita acento procurando.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import bases
from ..db import dependencia_sessao
from ..esquemas import ParticipanteSaida
from ..tabelas import Linha, PessoaDaBase

rotas = APIRouter(prefix="/participantes", tags=["participantes"])


@rotas.get("", response_model=list[ParticipanteSaida])
def buscar(q: str = Query("", min_length=0),
           limite: int = Query(20, le=100),
           linha: int | None = None,
           grupo: str | None = None,
           db: Session = Depends(dependencia_sessao)) -> list[dict]:
    lista: frozenset[str] | None = None
    if linha is not None:
        achada = db.get(Linha, linha)
        if achada is None:
            raise HTTPException(404, "linha não encontrada")
        ids = bases.ids_da_folha(db, achada.folha)
        lista = bases.lista_da_folha(db, achada.folha)
    else:
        base, lista_base = bases.para_identificar(db, grupo)
        ids = [b.id for b in (base, lista_base) if b is not None]
        lista = bases.matriculas(db, lista_base.id) if lista_base else None
    if not ids:
        return []
    consulta = (select(PessoaDaBase).where(PessoaDaBase.base_id.in_(ids))
                .order_by(PessoaDaBase.nome).limit(limite * len(ids)))
    if q.strip():
        # Só dígitos normalizam para vazio, e `LIKE '%%'` casava com a base inteira: buscar
        # uma matrícula devolvia 20 pessoas quaisquer, com a certa no meio (A63).
        filtros = [PessoaDaBase.matricula.like(f"{q.strip()}%")]
        if nome := bases._maiusculas(q):
            filtros.append(PessoaDaBase.nome_normalizado.like(f"%{nome}%"))
        consulta = consulta.where(or_(*filtros))
    # a mesma pessoa na geral e na lista aparece uma vez, com a marca da lista (A66)
    vistas, saida = set(), []
    for p in db.scalars(consulta):
        if p.matricula in vistas:
            continue
        vistas.add(p.matricula)
        saida.append({"matricula": p.matricula, "nome": p.nome, "curso": p.curso,
                      "na_lista": (p.matricula in lista) if lista is not None else None})
    return saida[:limite]
