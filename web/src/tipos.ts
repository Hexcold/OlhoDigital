// Espelho dos esquemas Pydantic de `api/esquemas.py`.
//
// Escritos à mão de propósito, por enquanto: gerar estes tipos do OpenAPI é
// possível e provavelmente será feito, mas isso só paga quando o contrato
// parar de mudar toda sessão. Enquanto muda, o gerador esconde a mudança em
// vez de mostrá-la.

/** O eixo do PROCESSAMENTO. Não diz nada sobre revisão — ver `Situacao`. */
export type EstadoFolha =
  | "recebida"
  | "processando"
  | "processada"
  /** Leu sem falhar, mas com a assinatura de recorte errado: várias linhas e
   *  nenhum aceite. Os dados ficam e a revisão é possível — só não se deve
   *  confundir isto com leitura boa. */
  | "suspeita"
  /** Não tem os 4 marcadores ArUco — provavelmente não é folha de presença. */
  | "nao_e_folha"
  | "erro";

/** A etiqueta única que a tela mostra, combinando processamento e revisão.
 *  Derivada no servidor: o cliente não tem como saber quantas pendências uma
 *  folha tem sem pedir as linhas dela, uma a uma. */
export type Situacao =
  | "recebida"
  | "processando"
  | "erro"
  | "suspeita"
  | "nao_e_folha"
  /** Processada com zero linhas: ninguém preencheu. Não é erro nem pendência. */
  | "em_branco"
  | "aguardando_revisao"
  /** Sem quarentena em aberto, e sem ninguém ter conferido. */
  | "pronta"
  | "revisada";
export type Decisao = "aceito" | "quarentena" | "anulada";

/** Por que uma célula não virou caractere. Distingue "não escreveu" de
 *  "escreveu e não deu para ler" — foi essa distinção que revelou que a
 *  m5_01-06 era uma linha abandonada, não uma leitura ruim. */
export type Motivo = "lido" | "vazia" | "sem_caixa";

/** Categorias de horas complementares. Lista fechada porque ela SOMA no
 *  relatório — texto livre viraria EXTENSAO/Extensão/extensao. Espelha
 *  `esquemas.CATEGORIAS_EVENTO`. */
export const CATEGORIAS = [
  "ensino",
  "pesquisa",
  "extensao",
  "cultural",
  "esportiva",
  "outra",
] as const;
export type Categoria = (typeof CATEGORIAS)[number];
export type TipoEvento = "aula" | "evento";

/** Uma OCORRÊNCIA de coleta — o que a chave da folha aponta (D15).
 *
 *  A unidade é a sala numa janela de tempo, não a palestra: cinco
 *  apresentações das 8h às 10h na mesma sala são um evento só, e várias folhas
 *  dividem a chave dele. */
export interface Evento {
  id: number;
  chave: string;
  tipo: TipoEvento;
  nome: string;
  responsavel: string | null;
  local: string | null;
  inicio: string | null;
  fim: string | null;
  situacao: "em andamento" | "encerrado";
  disciplina: string | null;
  turma: string | null;
  semestre: string | null;
  categoria: Categoria | null;
  horas_complementares: number | null;
  orgao_promotor: string | null;
  criado_em: string;
  /** Contagem, não a lista. O nome não é `folhas` de propósito: no servidor
   *  esse nome é o relacionamento do ORM, e a colisão fez o Pydantic devolver
   *  a lista no lugar do número. */
  folhas_enviadas: number;
}

export interface Folha {
  id: number;
  evento_id: number | null;
  /** A chave como foi lida, com "?" no que não deu para ler. */
  chave_lida: string | null;
  /** Como o vínculo foi feito. Nulo é folha sem evento, na bandeja. */
  origem_vinculo: "automatico" | "confirmado" | "corrigido" | "informado" | null;
  evento_chave: string | null;
  nome_original: string;
  /** Um PDF de escâner traz uma folha por página — o formato real das 500. */
  pagina: number;
  modelo_folha: string;
  estado: EstadoFolha;
  erro: string | null;
  linhas_preenchidas: number;
  linhas_anuladas: number;
  // o ponto de operação com que ESTA folha foi lida. Viaja junto do resultado
  // porque é constante de calibração: sem ele ninguém sabe, dois meses depois,
  // se a folha foi lida com a CNN base ou com a afinada.
  alpha: number | null;
  theta: number | null;
  delta: number | null;
  metrica_nome: string | null;
  modelo_matricula: string | null;
  modelo_letras: string | null;
  criado_em: string;
  processada_em: string | null;
  /** Carimbo de revisão concluída. Distingue "ninguém abriu esta folha" de
   *  "esta folha foi conferida" — as duas têm zero pendências. */
  revisada_em: string | null;
  revisada_por: string | null;

  /** Derivados no servidor — ver `Situacao`. */
  situacao: Situacao;
  /** Linhas de quarentena sem decisão. É o que impede concluir. */
  pendentes: number;
  /** Linhas cuja matrícula aparece em outra linha desta mesma folha (A40). */
  duplicadas: number;
}

export interface Candidato {
  matricula: string;
  nome: string;
  curso: string | null;
  score: number;
}

export interface Linha {
  id: number;
  folha_id: number;
  numero: number;
  leitura_matricula: string;
  leitura_nome: string;
  confiancas_matricula: number[];
  confiancas_nome: number[];
  motivos_matricula: Motivo[];
  motivos_nome: Motivo[];
  score: number;
  margem: number;
  decisao: Decisao;
  matricula_sugerida: string | null;
  candidatos: Candidato[];
  /** A mesma matrícula aparece em outra linha DESTA folha. Não é erro de
   *  identificação — as duas linhas podem apontar para a pessoa certa —, então
   *  nenhuma métrica do sistema enxerga. O estrago é na frequência. */
  duplicada_na_folha: boolean;

  /** Matrículas REAIS a um dígito da escolhida. É a medida de perigo de A15:
   *  um erro de leitura cai em pessoa existente, o score sai alto, a margem
   *  sai folgada, e nem θ nem δ percebem. 62,6% da base tem ao menos uma. */
  vizinhos: string[];
  /** Por que a linha caiu — score baixo e margem apertada pedem atenção
   *  diferente. Vazio quando a linha foi aceita. */
  motivo_rejeicao: string;
  /** A ÚLTIMA decisão humana, se houver. A tabela é append-only, então
   *  revisar de novo empilha e o que vale é o topo. */
  revisao: Revisao | null;
}

export interface Revisao {
  id: number;
  linha_id: number;
  /** null é decisão legítima — "não identificado" —, não ausência de decisão. */
  matricula_escolhida: string | null;
  revisor: string | null;
  ms: number | null;
  criado_em: string;
}

export interface ResumoRevisao {
  linhas: number;
  identificados: number;
  nao_identificados: number;
  anuladas: number;
  /** Quarentena sem decisão. É o que impede concluir. */
  pendentes: number;
  duplicadas: number;
}

export interface Participante {
  matricula: string;
  nome: string;
  curso: string | null;
}

/** Os textos editáveis da folha. São os únicos campos que o servidor aceita. */
export interface CabecalhoFolha {
  titulo: string;
  subtitulo: string;
  complemento: string;
  instrucao: string;
}

export interface CabecalhoResposta {
  cabecalho: CabecalhoFolha;
  limite_caracteres: number;
  /** O que o editor não alcança. Vem do servidor, que é quem garante. */
  travado: string[];
}

export interface ItemLote {
  linha: number;
  chave: string;
  folhas: number;
  nome: string | null;
  local: string | null;
  responsavel: string | null;
  /** AAAA-MM-DD. Vai no carimbo da folha; sem ele, o carimbo é só "v4". */
  dia: string | null;
}

export interface Lote {
  itens: ItemLote[];
  /** Impedem gerar o PDF. */
  erros: string[];
  /** Exigem confirmação antes de gerar. */
  alertas: string[];
  total_chaves: number;
  total_folhas: number;
}

export interface Cadastro {
  criados: number;
  ja_existiam: number;
}

export interface EventoSugerido {
  id: number;
  chave: string;
  nome: string;
  distancia: number;
}

/** Por que a folha ficou sem evento, e os eventos do mais provável ao menos. */
export interface SugestaoEventos {
  motivo: string;
  candidatos: EventoSugerido[];
}

export interface Sessao {
  usuario: string;
  papel: "admin" | "impressao";
  /** Falso no desenvolvimento local, onde não há login. */
  login: boolean;
}
