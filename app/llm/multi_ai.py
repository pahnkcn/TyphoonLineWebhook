"""
Multi-AI Consensus Engine

Sends user messages to multiple AI providers in parallel,
cross-evaluates responses, and returns the highest-scored answer.
"""
import json
import logging
import time
import re
import concurrent.futures
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    APIStatusError,
)

from .providers import AIProvider, ProviderRegistry, get_registry

logger = logging.getLogger(__name__)

# Timeouts
GENERATION_TIMEOUT = 60  # seconds per provider for generation
EVALUATION_TIMEOUT = 80  # seconds per provider for evaluation

# Thread pool for parallel AI calls
_MULTI_AI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=12, thread_name_prefix="multi-ai"
)

# Evaluation prompt template
EVALUATION_PROMPT = """คุณคือผู้เชี่ยวชาญด้านการให้รหัส (Coder) ตามระบบ MITI 4.2.1 (Motivational Interviewing Treatment Integrity)
หน้าที่ของคุณคือการวิเคราะห์และให้คะแนน 0-100 สำหรับทุก "คำตอบตัวเลือก" ต่อไปนี้ (จำลองว่าตัวเลือกคือ Clinician และผู้ใช้คือ Client)
คุณต้องให้คะแนนครบทุกตัวเลือก ห้ามข้าม

ขั้นตอนการประเมินในใจ (ประยุกต์จาก MITI 4.2.1 และ RAG):
1. การนับพฤติกรรม (Behavior Counts):
   - ให้คะแนนสูง (MI Adherent): Emphasizing Autonomy (เน้นสิทธิการตัดสินใจ), Seeking Collaboration (ขอความร่วมมือ), Affirm (ชื่นชมจุดแข็งอย่างลึกซึ้ง), Complex Reflection (สะท้อนความหมายที่ซ่อนอยู่)
   - ให้คะแนนปานกลาง: Simple Reflection, Question (ควรถามปลายเปิด), Giving Information (ให้ข้อมูลเป็นกลาง)
   - หักคะแนน (MI Non-Adherent): Confront (โต้แย้ง/ตำหนิ/วิจารณ์/สั่งสอน), Persuade (โน้มน้าว/แนะนำโดยไม่ขออนุญาต)
2. คะแนนภาพรวม (Global Ratings):
   - Cultivating Change Talk: พยายามกระตุ้นให้ผู้ใช้พูดถึงเป้าหมาย/การเปลี่ยนแปลง
   - Softening Sustain Talk: เลี่ยงการไปปะทะหรือลดความสำคัญของข้ออ้างที่จะไม่เปลี่ยน
   - Partnership: ร่วมมือ แชร์อำนาจ ไม่ทำตัวเหนือกว่า
   - Empathy: เข้าใจมุมมองและความรู้สึกที่ไม่ได้พูดออกมาตรงๆ
3. ความถูกต้องของบริบท (RAG Knowledge):
   - หากมี "บริบทความรู้จากระบบ" แนบมา ต้องนำข้อมูลมาสังเคราะห์ใช้ (Giving Information แบบเหมาะสม) อย่างถูกต้อง เป็นธรรมชาติ และไม่ทิ้งหลักการ MI

บทบาทของแชทบอท: \"\"\"{system_context}\"\"\"

คำถามผู้ใช้ (Client): \"\"\"{user_message}\"\"\"

คำตอบตัวเลือก (Clinician Responses):
{responses_section}

วิเคราะห์ตามเกณฑ์ข้างต้น แล้วรวมผลลัพธ์เป็นคะแนน 0-100 สำหรับแต่ละคำตอบ
ตอบผลลัพธ์เป็น JSON เท่านั้น ครบทุก key ห้ามมีคำอธิบายหรือข้อความอื่น:
{expected_json}"""


@dataclass
class ProviderResponse:
    """Response from a single AI provider."""
    provider_name: str
    content: str
    response_time_ms: float
    success: bool = True
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class EvaluationResult:
    """Scores from a single evaluator for all responses."""
    evaluator_name: str
    scores: Dict[str, float] = field(default_factory=dict)
    success: bool = True
    error: str = ""
    response_time_ms: float = 0.0


@dataclass
class ConsensusResult:
    """Final result of the multi-AI consensus process."""
    best_response: str
    best_provider: str
    avg_score: float
    all_scores: Dict[str, float]
    all_responses: Dict[str, str]
    generation_time_ms: float
    evaluation_time_ms: float
    total_time_ms: float
    providers_used: int
    evaluation_rounds: int
    token_usage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    provider_times: Dict[str, Dict[str, float]] = field(default_factory=dict)


def _generate_single(
    registry: ProviderRegistry,
    provider: AIProvider,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> ProviderResponse:
    """Generate a response from a single provider."""
    start = time.time()
    try:
        client = registry.get_client(provider)
        resp = client.chat.completions.create(
            model=provider.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = ""
        if resp and resp.choices and len(resp.choices) > 0:
            msg = resp.choices[0].message
            if msg and msg.content:
                content = msg.content.strip()

        if not content:
            raise ValueError("Empty response content")

        # Extract token usage from response
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if resp.usage:
            prompt_tokens = resp.usage.prompt_tokens or 0
            completion_tokens = resp.usage.completion_tokens or 0
            total_tokens = resp.usage.total_tokens or (prompt_tokens + completion_tokens)
        else:
            logger.warning(f"[multi-ai] {provider.name} returned no usage data")

        elapsed = (time.time() - start) * 1000
        logger.info(
            f"[multi-ai] {provider.name} generated {len(content)} chars "
            f"in {elapsed:.0f}ms tokens=[{prompt_tokens}/{completion_tokens}/{total_tokens}]"
        )
        return ProviderResponse(
            provider_name=provider.name,
            content=content,
            response_time_ms=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    except (APITimeoutError, APIConnectionError) as e:
        elapsed = (time.time() - start) * 1000
        logger.warning(f"[multi-ai] {provider.name} connection error: {e}")
        return ProviderResponse(
            provider_name=provider.name,
            content="",
            response_time_ms=elapsed,
            success=False,
            error=f"Connection error: {type(e).__name__}",
        )
    except RateLimitError as e:
        elapsed = (time.time() - start) * 1000
        logger.warning(f"[multi-ai] {provider.name} rate limited: {e}")
        return ProviderResponse(
            provider_name=provider.name,
            content="",
            response_time_ms=elapsed,
            success=False,
            error="Rate limited",
        )
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.error(f"[multi-ai] {provider.name} generation error: {e}")
        return ProviderResponse(
            provider_name=provider.name,
            content="",
            response_time_ms=elapsed,
            success=False,
            error=str(e),
        )


def generate_all(
    messages: List[Dict[str, str]],
    registry: Optional[ProviderRegistry] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: float = GENERATION_TIMEOUT,
) -> List[ProviderResponse]:
    """Send messages to all active providers in parallel.

    Returns:
        List of ProviderResponse (both successful and failed).
    """
    if registry is None:
        registry = get_registry()

    providers = registry.get_active_providers()
    if not providers:
        logger.error("[multi-ai] No active providers available")
        return []

    futures = {}
    for provider in providers:
        future = _MULTI_AI_EXECUTOR.submit(
            _generate_single, registry, provider, messages, temperature, max_tokens
        )
        futures[future] = provider.name

    results: List[ProviderResponse] = []
    done, not_done = concurrent.futures.wait(
        futures.keys(), timeout=timeout + 5
    )

    for future in done:
        try:
            result = future.result(timeout=1)
            results.append(result)
        except Exception as e:
            name = futures[future]
            logger.error(f"[multi-ai] {name} future error: {e}")
            results.append(ProviderResponse(
                provider_name=name, content="", response_time_ms=0,
                success=False, error=str(e),
            ))

    for future in not_done:
        name = futures[future]
        future.cancel()
        logger.warning(f"[multi-ai] {name} timed out")
        results.append(ProviderResponse(
            provider_name=name, content="", response_time_ms=timeout * 1000,
            success=False, error="Timeout",
        ))

    return results


def _build_evaluation_prompt(
    user_message: str,
    responses: Dict[str, str],
    exclude_provider: str,
    system_context: str = "",
) -> str:
    """Build the evaluation prompt for a single evaluator."""
    # Assign letter labels (A, B, C, ...) to providers to evaluate
    labels = {}
    responses_section_parts = []
    idx = 0

    for provider_name, content in responses.items():
        if provider_name == exclude_provider:
            continue
        label = chr(ord("A") + idx)
        labels[label] = provider_name
        responses_section_parts.append(
            f'คำตอบ {label}:\n"""{content}"""'
        )
        idx += 1

    responses_section = "\n\n".join(responses_section_parts)
    expected_json = json.dumps(
        {label: 85 for label in sorted(labels.keys())},
        ensure_ascii=False,
    )

    prompt = EVALUATION_PROMPT.format(
        system_context=system_context,
        user_message=user_message,
        responses_section=responses_section,
        expected_json=expected_json,
    )

    return prompt, labels


def _parse_evaluation_json(raw: str, expected_labels: List[str]) -> Dict[str, float]:
    """Parse evaluation JSON from AI response, with fallback regex."""
    scores: Dict[str, float] = {}
    raw_stripped = raw.strip()

    # Try to extract JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_stripped, re.DOTALL)
    if json_match:
        raw_stripped = json_match.group(1)

    # Try to find raw JSON object if text doesn't start with {
    if not raw_stripped.startswith("{"):
        brace_match = re.search(r'\{[^{}]+\}', raw_stripped)
        if brace_match:
            raw_stripped = brace_match.group(0)

    # Phase 1: Try JSON parse
    try:
        parsed = json.loads(raw_stripped)
        if isinstance(parsed, dict):
            for label in expected_labels:
                val = parsed.get(label)
                if val is not None:
                    try:
                        score = float(val)
                        scores[label] = max(0.0, min(100.0, score))
                    except (ValueError, TypeError):
                        pass
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Phase 2: Regex fallback for any labels still missing
    missing = [l for l in expected_labels if l not in scores]
    if missing:
        for label in missing:
            pattern = rf'["\']?{label}["\']?\s*:\s*(\d+(?:\.\d+)?)'
            match = re.search(pattern, raw)
            if match:
                score = float(match.group(1))
                scores[label] = max(0.0, min(100.0, score))

    return scores


def _evaluate_single(
    registry: ProviderRegistry,
    evaluator: AIProvider,
    user_message: str,
    responses: Dict[str, str],
    system_context: str = "",
) -> EvaluationResult:
    """Have a single provider evaluate all other providers' responses."""
    start = time.time()
    try:
        prompt, labels = _build_evaluation_prompt(
            user_message, responses, exclude_provider=evaluator.name,
            system_context=system_context,
        )

        if not labels:
            return EvaluationResult(
                evaluator_name=evaluator.name,
                scores={},
                success=False,
                error="No responses to evaluate",
            )

        client = registry.get_client(evaluator)
        resp = client.chat.completions.create(
            model=evaluator.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "คุณเป็นผู้เชี่ยวชาญในการประเมินคุณภาพคำตอบ "
                        "ตอบเป็น JSON เท่านั้น"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=512,
        )

        content = ""
        if resp and resp.choices and len(resp.choices) > 0:
            msg = resp.choices[0].message
            if msg and msg.content:
                content = msg.content.strip()

        if not content:
            raise ValueError("Empty evaluation response")

        logger.debug(
            f"[multi-ai] {evaluator.name} raw evaluation response: "
            f"{content[:500]}"
        )

        expected_labels = sorted(labels.keys())
        raw_scores = _parse_evaluation_json(content, expected_labels)

        if not raw_scores:
            logger.warning(
                f"[multi-ai] {evaluator.name} returned unparseable evaluation: "
                f"{content[:200]}"
            )
            return EvaluationResult(
                evaluator_name=evaluator.name,
                scores={},
                success=False,
                error="Failed to parse evaluation JSON",
            )

        # Warn if some labels were not found
        missing_labels = [l for l in expected_labels if l not in raw_scores]
        if missing_labels:
            logger.warning(
                f"[multi-ai] {evaluator.name} partial evaluation — "
                f"missing labels {missing_labels}. Raw: {content[:300]}"
            )

        # Map label scores back to provider names
        provider_scores = {}
        for label, score in raw_scores.items():
            if label in labels:
                provider_scores[labels[label]] = score

        elapsed = (time.time() - start) * 1000
        logger.info(
            f"[multi-ai] {evaluator.name} evaluated: {provider_scores} "
            f"in {elapsed:.0f}ms"
        )
        return EvaluationResult(
            evaluator_name=evaluator.name,
            scores=provider_scores,
            response_time_ms=elapsed,
        )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.error(f"[multi-ai] {evaluator.name} evaluation error: {e}")
        return EvaluationResult(
            evaluator_name=evaluator.name,
            scores={},
            success=False,
            error=str(e),
            response_time_ms=elapsed,
        )


def cross_evaluate(
    user_message: str,
    responses: Dict[str, str],
    registry: Optional[ProviderRegistry] = None,
    system_context: str = "",
    timeout: float = EVALUATION_TIMEOUT,
) -> List[EvaluationResult]:
    """Have each provider evaluate all other providers' responses in parallel.

    Args:
        user_message: The original user message.
        responses: Dict mapping provider_name -> response content.
        registry: Optional ProviderRegistry (uses global if not provided).

    Returns:
        List of EvaluationResult from each evaluator.
    """
    if registry is None:
        registry = get_registry()

    # Only use providers that successfully generated a response as evaluators
    active_providers = registry.get_active_providers()
    evaluators = [p for p in active_providers if p.name in responses]

    if len(evaluators) < 2:
        logger.info("[multi-ai] Fewer than 2 evaluators, skipping cross-evaluation")
        return []

    futures = {}
    for evaluator in evaluators:
        future = _MULTI_AI_EXECUTOR.submit(
            _evaluate_single, registry, evaluator, user_message, responses, system_context
        )
        futures[future] = evaluator.name

    results: List[EvaluationResult] = []
    done, not_done = concurrent.futures.wait(
        futures.keys(), timeout=timeout + 5
    )

    for future in done:
        try:
            result = future.result(timeout=1)
            results.append(result)
        except Exception as e:
            name = futures[future]
            logger.error(f"[multi-ai] {name} evaluation future error: {e}")

    for future in not_done:
        name = futures[future]
        future.cancel()
        logger.warning(f"[multi-ai] {name} evaluation timed out")

    return results


def aggregate_scores(
    responses: Dict[str, str],
    evaluations: List[EvaluationResult],
) -> Dict[str, float]:
    """Aggregate evaluation scores into average scores per provider.

    Returns:
        Dict mapping provider_name -> average score.
    """
    score_sums: Dict[str, List[float]] = {name: [] for name in responses}

    for evaluation in evaluations:
        if not evaluation.success or not evaluation.scores:
            continue
        for provider_name, score in evaluation.scores.items():
            if provider_name in score_sums:
                score_sums[provider_name].append(score)

    avg_scores: Dict[str, float] = {}
    for provider_name, scores in score_sums.items():
        if scores:
            avg_scores[provider_name] = sum(scores) / len(scores)
        else:
            # No evaluations received — give a neutral baseline score
            avg_scores[provider_name] = 50.0

    return avg_scores


def select_best(
    responses: Dict[str, str],
    avg_scores: Dict[str, float],
) -> Tuple[str, str, float]:
    """Select the best response based on aggregated scores.

    Returns:
        Tuple of (best_provider_name, best_response_content, best_score).
    """
    if not avg_scores:
        # Fallback: return first response
        first_name = next(iter(responses))
        return first_name, responses[first_name], 0.0

    best_provider = max(avg_scores, key=avg_scores.get)
    return best_provider, responses[best_provider], avg_scores[best_provider]


def multi_ai_chat(
    messages: List[Dict[str, str]],
    registry: Optional[ProviderRegistry] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: float = 0,
) -> ConsensusResult:
    """Main orchestrator: generate → evaluate → select best response.

    Args:
        messages: Conversation messages in OpenAI format.
        registry: Optional ProviderRegistry (uses global if not provided).
        temperature: Generation temperature.
        max_tokens: Max tokens for generation.
        timeout: Overall time budget in seconds. 0 = use default constants.

    Returns:
        ConsensusResult with the best response and metadata.

    Raises:
        RuntimeError: If no providers generated a valid response.
    """
    if registry is None:
        registry = get_registry()

    total_start = time.time()

    # Derive per-phase timeouts from overall budget
    if timeout > 0:
        gen_timeout = timeout * 0.45
        eval_timeout = timeout * 0.45
    else:
        gen_timeout = GENERATION_TIMEOUT
        eval_timeout = EVALUATION_TIMEOUT

    # Extract system context and latest user message for evaluation prompt
    system_context = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_context = msg.get("content", "")
            break

    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    # Phase 1: Parallel Generation
    gen_start = time.time()
    gen_results = generate_all(messages, registry, temperature, max_tokens, timeout=gen_timeout)
    gen_elapsed = (time.time() - gen_start) * 1000

    successful_responses: Dict[str, str] = {}
    gen_token_usage: Dict[str, Dict[str, int]] = {}
    provider_times: Dict[str, Dict[str, float]] = {}
    for r in gen_results:
        if r.success and r.content:
            successful_responses[r.provider_name] = r.content
        if r.total_tokens > 0:
            gen_token_usage[r.provider_name] = {
                "prompt": r.prompt_tokens,
                "completion": r.completion_tokens,
                "total": r.total_tokens,
            }
        provider_times[r.provider_name] = {"gen_ms": round(r.response_time_ms, 0), "eval_ms": 0.0}

    if not successful_responses:
        failed_names = [r.provider_name for r in gen_results]
        raise RuntimeError(
            f"All providers failed to generate a response: {failed_names}"
        )

    logger.info(
        f"[multi-ai] Phase 1 complete: {len(successful_responses)}/"
        f"{len(gen_results)} providers succeeded in {gen_elapsed:.0f}ms"
    )

    # If only 1 successful response, return it directly (no evaluation)
    if len(successful_responses) == 1:
        only_name = next(iter(successful_responses))
        only_content = successful_responses[only_name]
        total_elapsed = (time.time() - total_start) * 1000
        logger.info(
            f"[multi-ai] Only 1 provider succeeded ({only_name}), "
            f"skipping evaluation"
        )
        return ConsensusResult(
            best_response=only_content,
            best_provider=only_name,
            avg_score=100.0,
            all_scores={only_name: 100.0},
            all_responses=successful_responses,
            generation_time_ms=gen_elapsed,
            evaluation_time_ms=0,
            total_time_ms=total_elapsed,
            providers_used=1,
            evaluation_rounds=0,
            token_usage=gen_token_usage,
            provider_times=provider_times,
        )

    # Cooldown between phases to avoid provider rate limits (e.g. Gemini 429)
    time.sleep(1.5)

    # Phase 2: Cross-Evaluation
    eval_start = time.time()
    evaluations = cross_evaluate(user_message, successful_responses, registry, system_context, timeout=eval_timeout)
    eval_elapsed = (time.time() - eval_start) * 1000

    for e in evaluations:
        if e.evaluator_name in provider_times:
            provider_times[e.evaluator_name]["eval_ms"] = round(e.response_time_ms, 0)
        else:
            provider_times[e.evaluator_name] = {"gen_ms": 0.0, "eval_ms": round(e.response_time_ms, 0)}
    successful_evals = [e for e in evaluations if e.success]
    logger.info(
        f"[multi-ai] Phase 2 complete: {len(successful_evals)}/"
        f"{len(evaluations)} evaluators succeeded in {eval_elapsed:.0f}ms"
    )

    # Phase 3: Aggregate & Select
    if successful_evals:
        avg_scores = aggregate_scores(successful_responses, evaluations)
        best_provider, best_response, best_score = select_best(
            successful_responses, avg_scores
        )
    else:
        # Evaluation failed — fall back to first response
        logger.warning(
            "[multi-ai] All evaluations failed, using first successful response"
        )
        avg_scores = {name: 50.0 for name in successful_responses}
        best_provider = next(iter(successful_responses))
        best_response = successful_responses[best_provider]
        best_score = 50.0

    total_elapsed = (time.time() - total_start) * 1000

    logger.info(
        f"[multi-ai] Consensus complete: best={best_provider} "
        f"score={best_score:.1f} total={total_elapsed:.0f}ms "
        f"scores={avg_scores}"
    )

    return ConsensusResult(
        best_response=best_response,
        best_provider=best_provider,
        avg_score=best_score,
        all_scores=avg_scores,
        all_responses=successful_responses,
        generation_time_ms=gen_elapsed,
        evaluation_time_ms=eval_elapsed,
        total_time_ms=total_elapsed,
        providers_used=len(successful_responses),
        evaluation_rounds=len(successful_evals),
        token_usage=gen_token_usage,
        provider_times=provider_times,
    )
