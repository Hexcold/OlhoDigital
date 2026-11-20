import cv2
import numpy as np
from color import Color
from numpy.typing import NDArray

# Os 4 marcadores da folha, pelo id: 0 topo esquerdo, 1 topo direito, 2 baixo esquerdo,
# 3 baixo direito.
IDS_FOLHA = frozenset({0, 1, 2, 3})


def _detectar(detector, img_data: NDArray[np.uint8]) -> dict[int, NDArray[np.float32]]:
    """{id: 4 cantos} dos marcadores da folha que o detector acha nesta imagem."""
    corners, ids, _ = detector.detectMarkers(img_data)  # type: ignore
    achados: dict[int, NDArray[np.float32]] = {}
    if ids is None:
        return achados
    for c, i in zip(corners, ids.flatten()):
        if int(i) in IDS_FOLHA and int(i) not in achados:
            achados[int(i)] = c[0]
    return achados


def detectar_marcadores(img_data: NDArray[np.uint8]
                        ) -> tuple[dict[int, NDArray[np.float32]], set[int]]:
    """Os marcadores da folha, com um segundo passe para o que faltar (A61).

    O primeiro passe é a detecção de sempre, na imagem original. Se achar os 4, acabou, e a
    folha é alinhada exatamente como antes deste segundo passe existir.

    Se faltar algum, detecta de novo numa cópia ERODIDA 3×3: cada pixel vira o mais escuro
    da vizinhança, o preto cresce 1 px e um risco branco de até ~2 px que atravessa a borda
    do marcador (vinco, falha de toner) some. Foi o que derrubou as p36 e p47 da coleta.
    Os bits brancos do marcador (~15 px) perdem 1 px de cada lado e continuam legíveis.

    Do segundo passe só entra o que FALTAVA: os marcadores do primeiro passe mantêm os
    cantos originais, e o deslocamento da erosão (mediana 1,4 px, máx 2,2 px nas 59 folhas
    boas, contra uma célula de ~44 px) fica restrito ao canto recuperado. A erosão serve só
    para achar o marcador; quem a usa lê a folha da imagem original.

    Devolve ({id: cantos}, ids recuperados no segundo passe).
    """
    detector = cv2.aruco.ArucoDetector(  # type: ignore
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),  # type: ignore
        cv2.aruco.DetectorParameters())
    achados = _detectar(detector, img_data)
    recuperados: set[int] = set()
    if not IDS_FOLHA <= set(achados):
        # 5×5 é demais: na p36 recuperou um marcador e perdeu outro (A61)
        erodida = cv2.erode(img_data, np.ones((3, 3), np.uint8))
        for i, c in _detectar(detector, erodida).items():
            if i not in achados:
                achados[i] = c
                recuperados.add(i)
    return achados, recuperados


# o canto EXTERNO de cada marcador, que é o que o warp usa: 0 → canto 0 (sup. esq.),
# 1 → canto 1 (sup. dir.), 3 → canto 2 (inf. dir.), 2 → canto 3 (inf. esq.)
CANTO_EXTERNO = {0: 0, 1: 1, 3: 2, 2: 3}


def estimar_quarto(externos: dict[int, NDArray[np.float32]]) -> int:
    """Completa, no lugar, o canto externo que falta pelos outros três (D29).

    Os 4 cantos externos formam um paralelogramo quando a folha só gira, desloca e muda de
    escala, que é o que um scanner faz: TL + BR = TR + BL. Não é exato no alimentador (erro
    mediana 5,4 px, máx 13 px nas 59 folhas da coleta, A61), por isso isto é último recurso
    e a folha leva a marca de qual canto foi estimado. Devolve o id estimado.
    """
    falta = set(IDS_FOLHA) - set(externos)
    assert len(falta) == 1, f"estimar_quarto precisa de 3 marcadores, tem {sorted(externos)}"
    i = falta.pop()
    e = externos
    externos[i] = {0: lambda: e[1] + e[2] - e[3], 3: lambda: e[1] + e[2] - e[0],
                   1: lambda: e[0] + e[3] - e[2], 2: lambda: e[0] + e[3] - e[1]}[i]()
    return i


class Align:
    # D29: com 3 marcadores, estimar o quarto em vez de recusar a folha. DESLIGADO por
    # padrão: os scripts de experimento continuam exigindo os 4, para nenhum número medido
    # mudar em silêncio. O servidor liga em `api.pipeline.preparar`.
    ESTIMAR_QUARTO = False

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.recuperados: set[int] = set()
        self.estimado: int | None = None


    # A folha possui 4 ArUcos nos cantos para alinhamento
    # Eles são identificados pelos IDs:
    # 0 - Topo Esquerdo
    # 1 - Topo Direito
    # 2 - Baixo Esquerdo
    # 3 - Baixo Direito
    def aruco(self, img_data: NDArray[np.uint8]) -> NDArray[np.uint8]:
        

        achados, self.recuperados = detectar_marcadores(img_data)
        externos = {i: c[CANTO_EXTERNO[i]] for i, c in achados.items()}
        self.estimado = None
        if len(externos) == 3 and Align.ESTIMAR_QUARTO:
            self.estimado = estimar_quarto(externos)
        if not IDS_FOLHA <= set(externos):
            raise Exception("Nem todos os 4 ArUcos foram detectados.")

        # os cantos externos ordenados como [TL, TR, BR, BL]
        pts = np.float32([externos[0], externos[1], externos[3], externos[2]])

        # tamanho final
        W, H = self.width, self.height

        dst = np.float32([ [0, 0], [W, 0], [W, H], [0, H] ]) # type: ignore

        # Matriz de transformação e warp
        M = cv2.getPerspectiveTransform(pts, dst) # type: ignore
        corrigida: NDArray[np.uint8] = cv2.warpPerspective(img_data, M, (W, H)) # type: ignore
        return corrigida