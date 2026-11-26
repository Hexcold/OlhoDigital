"""O mesmo papel escaneado mais de uma vez (D30, A61).

Outro escaneamento gera outros bytes, então a trava de arquivo repetido (sha256 + página) não o
vê, e cada cópia entra como folha nova, ligada ao mesmo evento: a frequência contava cada pessoa
duas ou três vezes.

**A comparação** é linha a linha, na mesma posição: a linha N de uma folha bate com a linha N da
outra quando a matrícula lida (Levenshtein ≥ 0,8) e o nome lido (a métrica do nome ≥ 0,7)
concordam. A semelhança é a fração de linhas que batem sobre a união dos números de linha.
Medido na coleta (A61): o mesmo papel em passadas diferentes deu ≥ 0,60, e papéis diferentes
deram ≤ 0,33. O limite fica no meio, 0,50, e a comparação só acontece **dentro do mesmo evento**:
a mesma pessoa na linha 1 de dois eventos é legítimo.

**Qual cópia vale:** uma folha marcada com `copia_de` não conta (frequência e quarentena do
grupo a ignoram). Na detecção, vale a que tem mais aceites, a não ser que a outra já tenha
trabalho humano (revisão ou carimbo de concluída): trabalho de gente não troca de lado sozinho.
Antes dos dois, desde 22/11, vale a lida com a base de pessoas que o grupo usa HOJE: depois de
trocar a base, a leitura antiga tinha mais aceites (a base maior) e voltava a valer sozinha.
Quem revisa pode trocar ("usar esta") ou desfazer ("não é cópia"). Nada é apagado.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import bases, pipeline
from .esquemas import REVISOR_AUTOMATICO
from .tabelas import Folha, Linha, Revisao

LIMIAR_REENVIO = 0.50
SIM_MATRICULA = 0.8
SIM_NOME = 0.7


def semelhanca(a: dict[int, tuple[str, str]], b: dict[int, tuple[str, str]]) -> float | None:
    """Fração das linhas (união dos números) que batem na mesma posição. None sem linhas."""
    pipeline.preparar()
    from fuzzy import METRICA_NOME, SIM_NOME as METRICAS, sim_levenshtein   # noqa: E402

    sim_nome = METRICAS[METRICA_NOME]
    numeros = set(a) | set(b)
    if not numeros:
        return None
    batem = sum(1 for n in numeros if n in a and n in b
                and sim_levenshtein(a[n][0], b[n][0]) >= SIM_MATRICULA
                and sim_nome(a[n][1], b[n][1]) >= SIM_NOME)
    return batem / len(numeros)


def _leituras(db: Session, folha_id: int) -> dict[int, tuple[str, str]]:
    return {n: (m, nm) for n, m, nm in db.execute(
        select(Linha.numero, Linha.leitura_matricula, Linha.leitura_nome)
        .where(Linha.folha_id == folha_id))}


def _aceites(db: Session, folha_id: int) -> int:
    return len(list(db.scalars(select(Linha.id).where(Linha.folha_id == folha_id,
                                                      Linha.decisao == "aceito"))))


def tem_trabalho_humano(db: Session, folha: Folha) -> bool:
    """Revisão de gente (o "não identificado" automático do D26 não conta) ou carimbo."""
    if folha.revisada_em is not None:
        return True
    return db.scalar(
        select(Revisao.id).join(Linha, Revisao.linha_id == Linha.id)
        .where(Linha.folha_id == folha.id,
               or_(Revisao.revisor.is_(None), Revisao.revisor != REVISOR_AUTOMATICO))
        .limit(1)) is not None


def _base_em_dia(db: Session, folha: Folha) -> bool:
    """Lida com a base que o grupo da folha usa hoje (`api/bases.py`)."""
    base, lista = bases.para_identificar(db, bases.grupo_da_folha(db, folha))
    # sem base registrada, a regra de `bases.base_da_folha`: a que valeria hoje; a lista
    # (A66) também tem de ser a de hoje
    return (base is not None and bases.base_da_folha(db, folha) == base.id
            and folha.lista_id == (lista.id if lista else None))


def soltar(db: Session, folha: Folha) -> None:
    """Desfaz as relações de cópia em que a folha está, antes de ela ser relida, religada a
    outro evento ou apagada. Se ela era a que valia, a melhor das cópias passa a valer."""
    folha.copia_de = folha.semelhanca_copia = None
    copias = list(db.scalars(select(Folha).where(Folha.copia_de == folha.id)))
    if not copias:
        return
    nova = max(copias, key=lambda f: (_base_em_dia(db, f), tem_trabalho_humano(db, f),
                                      _aceites(db, f.id), -f.id))
    nova.copia_de = nova.semelhanca_copia = None
    for c in copias:
        if c is not nova:
            c.copia_de = nova.id


def detectar(db: Session, folha: Folha) -> Folha | None:
    """Procura, no evento da folha, outra folha que seja o mesmo papel. Devolve a outra ou None.

    Só olha folhas processadas e que valem (as cópias já apontam para uma que vale). A folha
    marcada "não é cópia" por alguém não é remarcada.
    """
    if folha.evento_id is None or folha.nao_e_copia or folha.estado != "processada":
        return None
    minhas = _leituras(db, folha.id)
    melhor, valor = None, LIMIAR_REENVIO
    for outra in db.scalars(select(Folha).where(
            Folha.evento_id == folha.evento_id, Folha.id != folha.id,
            Folha.estado == "processada", Folha.copia_de.is_(None),
            Folha.nao_e_copia.is_(False))):
        s = semelhanca(minhas, _leituras(db, outra.id))
        if s is not None and s >= valor:
            melhor, valor = outra, s
    if melhor is None:
        return None

    # vale a lida com a base de hoje; empatando nisso, a de mais aceites, a não ser que a
    # outra já tenha trabalho de gente
    em_dia, outra_em_dia = _base_em_dia(db, folha), _base_em_dia(db, melhor)
    if em_dia != outra_em_dia:
        vale, copia = (folha, melhor) if em_dia else (melhor, folha)
    elif (not tem_trabalho_humano(db, melhor)
            and _aceites(db, folha.id) > _aceites(db, melhor.id)):
        vale, copia = folha, melhor
    else:
        vale, copia = melhor, folha
    for c in db.scalars(select(Folha).where(Folha.copia_de == copia.id)):
        c.copia_de = vale.id                       # as cópias da cópia seguem quem vale
    copia.copia_de, copia.semelhanca_copia = vale.id, valor
    vale.copia_de = vale.semelhanca_copia = None
    return melhor


def usar(db: Session, copia: Folha) -> None:
    """Quem revisa escolheu esta cópia: ela passa a valer, e a que valia vira cópia dela."""
    antiga = db.get(Folha, copia.copia_de) if copia.copia_de else None
    if antiga is None:
        return
    s = copia.semelhanca_copia
    for c in db.scalars(select(Folha).where(Folha.copia_de == antiga.id)):
        c.copia_de = copia.id
    copia.copia_de = copia.semelhanca_copia = None
    antiga.copia_de, antiga.semelhanca_copia = copia.id, s
