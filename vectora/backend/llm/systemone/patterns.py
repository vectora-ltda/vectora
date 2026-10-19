"""Registry versionado de Decision Patterns para o SystemOne Harness.

Cada pattern declara de forma imutável:
- Identidade (pattern_id, version, owner)
- Entrada estruturada (campos de evidência obrigatórios e limite de tamanho)
- Perguntas tipadas (Noul, Choice ou Score)
- Policy (risco mínimo, limiar de confiança, margem de ambiguidade, faixa de incerteza)
- Privacidade (envio remoto permitido ou restrito ao runtime local, retenção)
- Operação (modo padrão, deadline, orçamento de tokens, concorrência, prioridade interativa)
- Avaliação (dataset versionado, baseline e critérios de promoção)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.llm.systemone.errors import SystemOneValidationError
from backend.llm.systemone.types import (
    ChoiceQuestion,
    DecisionProviderType,
    NoulQuestion,
    QuestionType,
    ScoreQuestion,
    SystemOneQuestion,
)

PatternOperationMode = Literal["off", "shadow", "advisory", "enforced"]
RetentionPolicyType = Literal["none", "ephemeral", "audit_metadata_only"]


class PatternInputSpec(BaseModel):
    """Especificação de entrada estruturada e evidências obrigatórias do pattern."""

    model_config = ConfigDict(frozen=True)

    required_fields: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Campos obrigatórios no dicionário de estado antes da inferência.",
    )
    max_state_chars: int = Field(
        default=16000,
        gt=0,
        description="Tamanho máximo permitido para o estado serializado em caracteres.",
    )
    requires_state_version: bool = Field(
        default=False,
        description="Exige versão ou hash explícito da entidade observada para prevenir TOCTOU.",
    )


class PatternPolicySpec(BaseModel):
    """Parâmetros de policy e abstinência específicos do pattern."""

    model_config = ConfigDict(frozen=True)

    policy_version: str = "policy-v1"
    is_high_impact: bool = False
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    choice_ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    noul_uncertainty_band: tuple[float, float] = (0.45, 0.55)


class PatternPrivacySpec(BaseModel):
    """Regras de privacidade, transmissão remota e retenção para o pattern."""

    model_config = ConfigDict(frozen=True)

    allow_remote_providers: bool = Field(
        default=False,
        description="Se False, o estado nunca pode ser transmitido a provedores remotos.",
    )
    allowed_remote_providers: tuple[DecisionProviderType, ...] = Field(
        default_factory=tuple,
        description="Lista explícita de provedores remotos autorizados quando allow_remote_providers=True.",
    )
    retention: RetentionPolicyType = "audit_metadata_only"


class PatternOperationSpec(BaseModel):
    """Limites operacionais de execução, concorrência, orçamento e fallback."""

    model_config = ConfigDict(frozen=True)

    default_mode: PatternOperationMode = "shadow"
    deadline_ms: float = Field(default=5000.0, gt=0.0)
    max_tokens: int = Field(default=128, gt=0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_concurrency: int = Field(default=4, gt=0)
    interactive_priority: bool = True
    allow_fallback: bool = False
    max_retries: int = Field(default=1, ge=0, le=3)


class PatternEvaluationSpec(BaseModel):
    """Metadados de dataset, calibração, baseline e critérios de promoção."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    calibration_version: str = "uncalibrated-v1"
    baseline_description: str
    promotion_criteria: str


class DecisionPatternSpec(BaseModel):
    """Especificação completa e imutável de um Decision Pattern registrado."""

    model_config = ConfigDict(frozen=True)

    pattern_id: str = Field(..., min_length=1)
    version: str = Field(default="v1", min_length=1)
    owner: str = Field(default="core-decision-runtime", min_length=1)
    description: str = Field(..., min_length=1)
    primary_question_type: QuestionType
    questions: dict[str, SystemOneQuestion] = Field(..., min_length=1)
    input_spec: PatternInputSpec = Field(default_factory=PatternInputSpec)
    policy_spec: PatternPolicySpec = Field(default_factory=PatternPolicySpec)
    privacy_spec: PatternPrivacySpec = Field(default_factory=PatternPrivacySpec)
    operation_spec: PatternOperationSpec = Field(default_factory=PatternOperationSpec)
    evaluation_spec: PatternEvaluationSpec


def serialize_canonical_state(
    state: dict[str, Any] | str,
    spec: DecisionPatternSpec,
) -> tuple[str, str]:
    """Valida as evidências obrigatórias e serializa o estado de forma determinística.

    Retorna `(canonical_json_str, state_sha256_hex)`.
    Levanta `SystemOneValidationError` se faltarem campos obrigatórios ou se exceder o limite.
    """
    if isinstance(state, str):
        if spec.input_spec.required_fields:
            raise SystemOneValidationError(
                f"Pattern '{spec.pattern_id}' exige estado estruturado (dict) com campos: "
                f"{', '.join(spec.input_spec.required_fields)}"
            )
        canonical = state
    elif isinstance(state, dict):
        missing = [
            field
            for field in spec.input_spec.required_fields
            if field not in state or state[field] is None
        ]
        if missing:
            raise SystemOneValidationError(
                f"Campos obrigatórios ausentes para '{spec.pattern_id}': {', '.join(missing)}"
            )
        try:
            canonical = json.dumps(
                state,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise SystemOneValidationError(
                f"Estado estruturado para '{spec.pattern_id}' não é serializável em JSON"
            ) from exc
    else:
        raise SystemOneValidationError(
            f"Tipo de estado inválido para '{spec.pattern_id}': esperado dict ou str"
        )

    if len(canonical) > spec.input_spec.max_state_chars:
        raise SystemOneValidationError(
            f"Estado para '{spec.pattern_id}' excede o limite de "
            f"{spec.input_spec.max_state_chars} caracteres"
        )

    state_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, state_hash


class DecisionPatternRegistry:
    """Catálogo imutável em tempo de execução para consulta de patterns versionados."""

    def __init__(self, patterns: list[DecisionPatternSpec] | None = None) -> None:
        self._patterns: dict[tuple[str, str], DecisionPatternSpec] = {}
        self._latest_by_id: dict[str, DecisionPatternSpec] = {}
        for spec in patterns or []:
            self.register(spec)

    def register(self, spec: DecisionPatternSpec) -> None:
        key = (spec.pattern_id, spec.version)
        if key in self._patterns:
            raise ValueError(
                f"Pattern '{spec.pattern_id}' versão '{spec.version}' já está registrado."
            )
        self._patterns[key] = spec
        self._latest_by_id[spec.pattern_id] = spec

    def get(self, pattern_id: str, version: str | None = None) -> DecisionPatternSpec:
        if version is not None:
            spec = self._patterns.get((pattern_id, version))
            if spec is None:
                raise KeyError(
                    f"Pattern '{pattern_id}' na versão '{version}' não está registrado."
                )
            return spec
        spec = self._latest_by_id.get(pattern_id)
        if spec is None:
            raise KeyError(f"Pattern '{pattern_id}' não está registrado.")
        return spec

    def list_patterns(self) -> list[DecisionPatternSpec]:
        return list(self._patterns.values())


def build_default_patterns() -> list[DecisionPatternSpec]:
    """Constrói os 12 patterns canônicos do ecossistema Vectora."""
    return [
        DecisionPatternSpec(
            pattern_id="goal_completion_noul",
            version="v1",
            owner="engine.goal_mode",
            description=(
                "Estima a probabilidade de cumprimento do objetivo com base em "
                "evidências verificáveis e resultados dos quality gates; nunca substitui gates."
            ),
            primary_question_type="noul",
            questions={
                "goal_completed": NoulQuestion(
                    criterion="O objetivo proposto foi integralmente satisfeito pelas evidências observadas?",
                    instructions="Considere apenas evidências verificáveis e resultados objetivos dos gates.",
                ),
                "evidence_sufficient": NoulQuestion(
                    criterion="As evidências fornecidas são suficientes e verificáveis para sustentar a conclusão?",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("goal", "turn_summary", "gates_passed"),
                max_state_chars=16000,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.80,
                noul_uncertainty_band=(0.40, 0.60),
            ),
            privacy_spec=PatternPrivacySpec(
                allow_remote_providers=False,
            ),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=4000.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.goal_completion.v1",
                baseline_description="Conversational LLM judge (_judge_goal) + quality gates",
                promotion_criteria="Zero falsos positivos quando gates falham; Brier score inferior ao baseline.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="goal_stagnation_choice",
            version="v1",
            owner="engine.goal_mode",
            description="Recomenda continuar, solicitar contexto ou replanejar diante de sinais de estagnação.",
            primary_question_type="choice",
            questions={
                "disposition": ChoiceQuestion(
                    options=["continue", "request_context", "replan"],
                    instructions="Escolha a ação mais adequada dado o histórico recente de turnos e falhas de gate.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("goal", "turns_used", "gate_failure_streak"),
                max_state_chars=12000,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.75,
                choice_ambiguity_margin=0.08,
            ),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=3500.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.goal_stagnation.v1",
                baseline_description="Heurística de _SAME_GATE_FAILURE_THRESHOLD",
                promotion_criteria="Redução de turnos desperdiçados sem interromper execuções produtivas.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="tool_risk_assessment_score",
            version="v1",
            owner="engine.hitl",
            description="Pontua risco operacional de chamada de ferramenta; pode apenas elevar exigência de revisão.",
            primary_question_type="score",
            questions={
                "risk_score": ScoreQuestion(
                    criterion="Risco operacional de perda de dados, exfiltração ou impacto externo irreversível.",
                    min_score=0.0,
                    max_score=1.0,
                    instructions="0.0 representa leitura inócua local; 1.0 representa mutação destrutiva ou externa.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("tool_name", "args_summary", "permission_mode"),
                max_state_chars=8000,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.80,
            ),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=1500.0,
                interactive_priority=True,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.tool_risk.v1",
                baseline_description="Regras estáticas de should_require_approval",
                promotion_criteria="Zero redução de HITL obrigatório; recall >= 0.95 em chamadas destrutivas.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="kanban_transition_choice",
            version="v1",
            owner="scheduling.kanban",
            description="Recomenda transição manual válida de card Kanban ou abstenção explícita (no_recommendation).",
            primary_question_type="choice",
            questions={
                "target_status": ChoiceQuestion(
                    options=["ready", "triage", "review", "no_recommendation"],
                    instructions=(
                        "Selecione apenas um destino permitido para o estado atual do card "
                        "ou 'no_recommendation' quando não houver evidência suficiente."
                    ),
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=(
                    "task_id",
                    "current_status",
                    "allowed_targets",
                    "card_version",
                ),
                max_state_chars=8000,
                requires_state_version=True,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.80,
                choice_ambiguity_margin=0.10,
            ),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2500.0,
                interactive_priority=True,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.kanban_transition.v1",
                baseline_description="Regras determinísticas de MANUAL_TRANSITIONS",
                promotion_criteria="100% de respeito a MANUAL_TRANSITIONS e versão do card.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="kanban_block_classifier",
            version="v1",
            owner="scheduling.kanban",
            description="Classifica a causa de bloqueio de uma task entre os 4 BLOCK_KINDS canônicos.",
            primary_question_type="choice",
            questions={
                "block_kind": ChoiceQuestion(
                    options=["dependency", "needs_input", "capability", "transient"],
                    instructions="Classifique estritamente dentro da taxonomia BLOCK_KINDS do Kanban.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("task_id", "error_summary"),
                max_state_chars=8000,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.75,
                choice_ambiguity_margin=0.05,
            ),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2500.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.kanban_block_kind.v1",
                baseline_description="Default estático needs_input",
                promotion_criteria="Macro-F1 superior ao default nas 4 classes de bloqueio.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="subagent_routing_choice",
            version="v1",
            owner="scheduling.delegate",
            description="Seleciona o perfil de subagente previamente autorizado mais adequado para a tarefa.",
            primary_question_type="choice",
            questions={
                "profile": ChoiceQuestion(
                    options=[
                        "generalist",
                        "researcher",
                        "coder",
                        "reviewer",
                        "no_recommendation",
                    ],
                    instructions="Escolha apenas entre perfis autorizados ou 'no_recommendation'.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("task_description", "authorized_profiles"),
                max_state_chars=8000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.75),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2000.0,
                interactive_priority=True,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.subagent_routing.v1",
                baseline_description="Seleção manual/default de perfil",
                promotion_criteria="Zero seleção fora de authorized_profiles; ganho de taxa de sucesso.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="rag_rerank_scoring",
            version="v1",
            owner="tools.rag",
            description="Atribui score de relevância [0.0, 1.0] a candidatos de busca já filtrados por permissão.",
            primary_question_type="score",
            questions={
                "relevance": ScoreQuestion(
                    criterion="Relevância semântica e evidencial do trecho para responder à consulta.",
                    min_score=0.0,
                    max_score=1.0,
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("query", "candidate_id", "snippet"),
                max_state_chars=12000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.65),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=3000.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.rag_rerank.v1",
                baseline_description="Ranking vetorial original / Cohere / Voyage",
                promotion_criteria="MRR@10 e NDCG@10 superiores ou equivalentes ao baseline dentro do SLO.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="evidence_support_choice",
            version="v1",
            owner="tools.rag",
            description="Classifica se as evidências recuperadas suportam, contradizem ou são insuficientes.",
            primary_question_type="choice",
            questions={
                "support": ChoiceQuestion(
                    options=[
                        "supported",
                        "contradicted",
                        "insufficient_evidence",
                    ],
                    instructions="Avalie estritamente com base nos trechos recuperados.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("claim", "evidence_snippets"),
                max_state_chars=12000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.80),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2500.0,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.evidence_support.v1",
                baseline_description="Sem verificação estruturada de suporte",
                promotion_criteria="Precisão >= 0.90 na detecção de contradição e falta de evidência.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="context_retention_choice",
            version="v1",
            owner="engine.compaction",
            description="Recomenda estratégia de retenção para bloco de contexto; obrigações críticas são preservadas por regra.",
            primary_question_type="choice",
            questions={
                "retention_action": ChoiceQuestion(
                    options=["retain_full", "summarize", "drop_non_critical"],
                    instructions="Nunca descarte instruções de sistema, restrições de segurança ou decisões pendentes.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("segment_summary", "segment_kind"),
                max_state_chars=10000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.75),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2000.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.context_retention.v1",
                baseline_description="Compactação por janela de tokens",
                promotion_criteria="Zero perda de instruções obrigatórias ou estado de aprovação.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="retry_disposition_choice",
            version="v1",
            owner="engine.resilience",
            description="Recomenda disposição diante de falha operacional sem jamais ampliar limites máximos.",
            primary_question_type="choice",
            questions={
                "disposition": ChoiceQuestion(
                    options=["retry_now", "backoff_wait", "escalate_hitl", "abort"],
                    instructions="Escolha a disposição respeitando o orçamento restante de tentativas.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("error_code", "attempt", "max_attempts"),
                max_state_chars=6000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.75),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=1500.0,
                interactive_priority=True,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.retry_disposition.v1",
                baseline_description="Backoff exponencial fixo por tipo de exceção",
                promotion_criteria="Redução de retries inúteis em erros permanentes.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="duplicate_task_noul",
            version="v1",
            owner="scheduling.kanban",
            description="Estima probabilidade de uma nova task ser duplicata de card ativo; não exclui nem funde sozinho.",
            primary_question_type="noul",
            questions={
                "is_duplicate": NoulQuestion(
                    criterion="A task proposta duplica o escopo e objetivo de um card ativo existente?",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=("candidate_title", "existing_cards_summary"),
                max_state_chars=10000,
            ),
            policy_spec=PatternPolicySpec(confidence_threshold=0.80),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2000.0,
                interactive_priority=False,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.duplicate_task.v1",
                baseline_description="Verificação textual simples de título",
                promotion_criteria="Brier score < 0.12 sem bloqueio indevido de tasks distintas.",
            ),
        ),
        DecisionPatternSpec(
            pattern_id="browser_dom_action_choice",
            version="v1",
            owner="tools.browser",
            description=(
                "Recomenda ação sobre observação recente do DOM vinculada à versão da página; "
                "publicação, envio e transações exigem aprovação humana."
            ),
            primary_question_type="choice",
            questions={
                "dom_action": ChoiceQuestion(
                    options=[
                        "click",
                        "type",
                        "scroll",
                        "wait",
                        "require_review",
                        "no_action",
                    ],
                    instructions="Vincule à observação atual; escolha 'require_review' para submissões ou efeitos externos.",
                ),
            },
            input_spec=PatternInputSpec(
                required_fields=(
                    "observation_version",
                    "page_url",
                    "candidate_elements",
                ),
                max_state_chars=16000,
                requires_state_version=True,
            ),
            policy_spec=PatternPolicySpec(
                confidence_threshold=0.85,
                choice_ambiguity_margin=0.10,
            ),
            privacy_spec=PatternPrivacySpec(allow_remote_providers=False),
            operation_spec=PatternOperationSpec(
                default_mode="shadow",
                deadline_ms=2500.0,
                interactive_priority=True,
            ),
            evaluation_spec=PatternEvaluationSpec(
                dataset_id="eval.browser_dom_action.v1",
                baseline_description="Inspeção conversacional do snapshot DOM",
                promotion_criteria="Invalidação 100% em mudança de observation_version; zero submissão sem HITL.",
            ),
        ),
    ]


DEFAULT_PATTERN_REGISTRY = DecisionPatternRegistry(build_default_patterns())
