"""O contradomínio: quem pode ser identificado (D07).

A busca livre existe para o caso que a A39 mediu custando 15,8 s: quando a
pessoa certa **não está** entre os candidatos ranqueados, o revisor precisa
procurá-la nas 1.334. Foi o que aconteceu na m7_02-05, com a matrícula lida com
cinco dígitos errados.

A busca é sobre `nome_normalizado` (D05: A–Z e espaço, acento removido por NFD)
porque 218 nomes da base têm acento e ninguém digita acento procurando.
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import config
from ..db import dependencia_sessao
from ..esquemas import ParticipanteSaida
from ..tabelas import Participante

rotas = APIRouter(prefix="/participantes", tags=["participantes"])


@rotas.get("", response_model=list[ParticipanteSaida])
def buscar(q: str = Query("", min_length=0),
           limite: int = Query(20, le=100),
           db: Session = Depends(dependencia_sessao)) -> list[Participante]:
    consulta = select(Participante).order_by(Participante.nome).limit(limite)
    if q:
        sys.path.insert(0, str(config.pasta_pipeline.parent / "experimento-fuzzy"))
        from normalizacao import normalizar                     # noqa: E402
        alvo = f"%{normalizar(q)}%"
        consulta = consulta.where(or_(Participante.nome_normalizado.like(alvo),
                                      Participante.matricula.like(f"{q}%")))
    return list(db.scalars(consulta))


@rotas.post("/importar", status_code=201)
def importar(db: Session = Depends(dependencia_sessao)) -> dict:
    """Carrega a base vigente do .xlsx para a tabela.

    A planilha continua sendo a fonte — trocá-la é decisão de projeto (D07), e
    A10 mostrou o que muda: 22/22 na base nova contra 7/22 na velha. Esta rota
    só espelha a fonte no banco; ela não decide qual base vale.
    """
    from ..pipeline import preparar
    preparar()
    from identificar_folha import BASE_XLSX                      # noqa: E402
    from base_alunos import carregar_base                        # noqa: E402
    from normalizacao import normalizar                          # noqa: E402

    pessoas = carregar_base(BASE_XLSX)
    novos = atualizados = 0
    for p in pessoas:
        existente = db.get(Participante, p["matricula"])
        if existente is None:
            db.add(Participante(matricula=p["matricula"], nome=p["nome"],
                                nome_normalizado=normalizar(p["nome"]),
                                curso=p.get("curso")))
            novos += 1
        else:
            existente.nome = p["nome"]
            existente.nome_normalizado = normalizar(p["nome"])
            existente.curso = p.get("curso")
            atualizados += 1
    db.commit()
    return {"na_planilha": len(pessoas), "novos": novos,
            "atualizados": atualizados}
