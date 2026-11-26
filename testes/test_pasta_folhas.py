"""`OLHODIGITAL_PASTA_FOLHAS` fora de `experimento-imagem/dados/` (bateria de 22/11).

O pipeline abre `dados/{nome}.png` relativo ao próprio diretório, e a API calculava o nome
com `relative_to(dados/)`. Com a pasta configurada em outro lugar, isso levantava ValueError
e as 62 folhas do lote caíram em erro. O nome agora sai de `relpath`, que sobe com `..`.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import apoio                                                   # noqa: F401  isola o ambiente

from api import pipeline
from api.config import config


class NomeNoPipeline(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(tempfile.mkdtemp(prefix="olhodigital_pasta_"))
        self.pipeline = self.raiz / "repo" / "experimento-imagem"
        (self.pipeline / "dados").mkdir(parents=True)
        p = mock.patch.object(config, "pasta_pipeline", self.pipeline)
        p.start()
        self.addCleanup(p.stop)

    def resolve(self, imagem: Path) -> Path:
        """O arquivo que o pipeline abriria, do jeito que ele monta o caminho."""
        nome = pipeline._nome_no_pipeline(imagem)
        return (self.pipeline / f"dados/{nome}.png").resolve()

    def test_dentro_de_dados_continua_igual(self):
        imagem = self.pipeline / "dados" / "enviadas" / "abc_p1.png"
        self.assertEqual(pipeline._nome_no_pipeline(imagem),
                         os.path.join("enviadas", "abc_p1"))
        self.assertEqual(self.resolve(imagem), imagem.resolve())

    def test_fora_de_dados_chega_no_mesmo_arquivo(self):
        imagem = self.raiz / "outro_disco" / "enviadas" / "abc_p1.png"
        self.assertEqual(self.resolve(imagem), imagem.resolve())


if __name__ == "__main__":
    unittest.main()
