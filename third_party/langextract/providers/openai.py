# Copyright 2025 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OpenAI provider for LangExtract."""
# pylint: disable=duplicate-code

from __future__ import annotations

import concurrent.futures
import dataclasses
from typing import Any, Iterator, Sequence

from langextract.core import base_model
from langextract.core import data
from langextract.core import exceptions
from langextract.core import schema
from langextract.core import types as core_types
from langextract.providers import patterns
from langextract.providers import router


@router.register(
    *patterns.OPENAI_PATTERNS,
    priority=patterns.OPENAI_PRIORITY,
)
@dataclasses.dataclass(init=False)
class OpenAILanguageModel(base_model.BaseLanguageModel):
  """Language model inference using OpenAI's API with structured output."""

  model_id: str = 'gpt-4o-mini'
  api_key: str | None = None
  base_url: str | None = None
  organization: str | None = None
  format_type: data.FormatType = data.FormatType.JSON
  temperature: float | None = None
  max_workers: int = 10
  _client: Any = dataclasses.field(default=None, repr=False, compare=False)
  _extra_kwargs: dict[str, Any] = dataclasses.field(
      default_factory=dict, repr=False, compare=False
  )

  @property
  def requires_fence_output(self) -> bool:
    """OpenAI JSON mode returns raw JSON without fences."""
    if self.format_type == data.FormatType.JSON:
      return False
    return super().requires_fence_output

  def __init__(
      self,
      model_id: str = 'gpt-4o-mini',
      api_key: str | None = None,
      base_url: str | None = None,
      organization: str | None = None,
      format_type: data.FormatType = data.FormatType.JSON,
      temperature: float | None = None,
      max_workers: int = 10,
      **kwargs,
  ) -> None:
    """Initialize the OpenAI language model.

    Args:
      model_id: The OpenAI model ID to use (e.g., 'gpt-4o-mini', 'gpt-4o').
      api_key: API key for OpenAI service.
      base_url: Base URL for OpenAI service.
      organization: Optional OpenAI organization ID.
      format_type: Output format (JSON or YAML).
      temperature: Sampling temperature.
      max_workers: Maximum number of parallel API calls.
      **kwargs: Ignored extra parameters so callers can pass a superset of
        arguments shared across back-ends without raising ``TypeError``.
    """
    # Lazy import: OpenAI package required
    try:
      # pylint: disable=import-outside-toplevel
      import openai
    except ImportError as e:
      raise exceptions.InferenceConfigError(
          'OpenAI provider requires openai package. '
          'Install with: pip install langextract[openai]'
      ) from e

    self.model_id = model_id
    self.api_key = api_key
    self.base_url = base_url
    self.organization = organization
    self.format_type = format_type
    self.temperature = temperature
    self.max_workers = max_workers

    if not self.api_key:
      raise exceptions.InferenceConfigError('API key not provided.')

    # Normalize API key and base_url
    if self.api_key:
      self.api_key = self.api_key.strip()
    if self.base_url:
      self.base_url = self.base_url.strip()
      # ✅ 修改：只对 OpenAI 官方和明确需要 /v1 的 API 添加 /v1 后缀
      # DeepSeek 的 base_url 是 https://api.deepseek.com（不需要 /v1）
      # Qwen 的 base_url 已包含 /compatible-mode/v1
      # 只有 OpenAI 官方或其他需要的才加 /v1
      if self.base_url and not self.base_url.rstrip("/").endswith("/v1"):
        # 检查是否为已知不需要 /v1 的 provider
        known_no_v1_domains = ["api.deepseek.com"]
        needs_v1 = not any(domain in self.base_url for domain in known_no_v1_domains)
        # 如果已包含 /compatible-mode/v1 等，也不要再加
        if needs_v1 and "/compatible-mode/v1" not in self.base_url:
          self.base_url = self.base_url.rstrip("/") + "/v1"

    # Initialize the OpenAI client
    print(f"[DEBUG OpenAILanguageModel.__init__] base_url={repr(self.base_url)}, type={type(self.base_url)}")
    print(f"[DEBUG OpenAILanguageModel.__init__] api_key前5位={self.api_key[:5] if self.api_key else 'None'}")
    self._client = openai.OpenAI(
        api_key=self.api_key,
        base_url=self.base_url,
        organization=self.organization,
        timeout=120.0,  # ✅ 添加超时设置，防止请求挂起
    )

    super().__init__(
        constraint=schema.Constraint(constraint_type=schema.ConstraintType.NONE)
    )
    self._extra_kwargs = kwargs or {}

  def _is_custom_provider(self) -> bool:
    """判断是否为非 OpenAI 官方 API（如 DeepSeek、Qwen）。

    Returns:
      True 如果是自定义 provider（需要禁用高级特性）
      False 如果是 OpenAI 官方 API（支持所有参数）
    """
    if not self.base_url:
      return False

    # OpenAI 官方域名列表
    official_domains = [
        "api.openai.com",
        "openai.azure.com",
    ]

    # 如果包含任何官方域名，则为官方 API
    return not any(domain in self.base_url for domain in official_domains)

  def _normalize_reasoning_params(self, config: dict) -> dict:
    """Normalize reasoning parameters for API compatibility.

    Converts flat 'reasoning_effort' to nested 'reasoning' structure.
    Merges with existing reasoning dict if present.
    """
    result = config.copy()

    if 'reasoning_effort' in result:
      effort = result.pop('reasoning_effort')
      reasoning = result.get('reasoning', {}) or {}
      reasoning.setdefault('effort', effort)
      result['reasoning'] = reasoning

    return result

  def _process_single_prompt(
      self, prompt: str, config: dict
  ) -> core_types.ScoredOutput:
    """Process a single prompt and return a ScoredOutput."""
    try:
      normalized_config = self._normalize_reasoning_params(config)

      system_message = ''
      if self.format_type == data.FormatType.JSON:
        system_message = (
            'You are a helpful assistant that responds in JSON format.'
        )
      elif self.format_type == data.FormatType.YAML:
        system_message = (
            'You are a helpful assistant that responds in YAML format.'
        )

      messages = [{'role': 'user', 'content': prompt}]
      if system_message:
        messages.insert(0, {'role': 'system', 'content': system_message})

      api_params = {
          'model': self.model_id,
          'messages': messages,
          'n': 1,
      }

      temp = normalized_config.get('temperature', self.temperature)
      if temp is not None:
        api_params['temperature'] = temp

      # ✅ 核心修改：根据 provider 类型决定使用哪些参数
      is_custom = self._is_custom_provider()

      if is_custom:
        # 自定义 provider（DeepSeek、Qwen 等）：支持基础 JSON mode
        print(f"[DEBUG] 使用自定义 provider 模式，启用 response_format")

        # ✅ 自定义 provider 支持 json_object 模式（根据测试结果）
        if self.format_type == data.FormatType.JSON:
          api_params['response_format'] = {'type': 'json_object'}

        # 基础参数仍然支持
        if (v := normalized_config.get('max_output_tokens')) is not None:
          api_params['max_tokens'] = v
        if (v := normalized_config.get('top_p')) is not None:
          api_params['top_p'] = v

        # 保守支持的参数（大部分兼容 API 都支持）
        for key in ['frequency_penalty', 'presence_penalty', 'seed', 'stop']:
          if (v := normalized_config.get(key)) is not None:
            api_params[key] = v

        # 仍然禁用的高级参数：
        # - reasoning (DeepSeek-R1 专用，可能不支持)
        # - logprobs, top_logprobs (可能不支持)
        # - json_schema (比 json_object 更严格，可能不支持)

      else:
        # OpenAI 官方 API：支持所有参数
        if self.format_type == data.FormatType.JSON:
          api_params.setdefault('response_format', {'type': 'json_object'})

        if (v := normalized_config.get('max_output_tokens')) is not None:
          api_params['max_tokens'] = v
        if (v := normalized_config.get('top_p')) is not None:
          api_params['top_p'] = v
        for key in [
            'frequency_penalty',
            'presence_penalty',
            'seed',
            'stop',
            'logprobs',
            'top_logprobs',
            'reasoning',
            'response_format',
        ]:
          if (v := normalized_config.get(key)) is not None:
            api_params[key] = v

      # ✅ 添加调试日志
      if is_custom:
        print(f"[DEBUG] API 请求参数（自定义 provider）: model={api_params.get('model')}, "
              f"temperature={api_params.get('temperature')}, "
              f"max_tokens={api_params.get('max_tokens')}, "
              f"response_format={api_params.get('response_format')}")

      response = self._client.chat.completions.create(**api_params)

      # Extract the response text using the v1.x response format
      output_text = response.choices[0].message.content

      # ✅ 调试：打印返回内容的前200字符
      if is_custom and output_text:
        print(f"[DEBUG] API 返回（前200字符）: {output_text[:200]}")

      return core_types.ScoredOutput(score=1.0, output=output_text)

    except Exception as e:
      # ✅ 增强错误信息，便于调试
      error_msg = f'OpenAI API error (base_url={self.base_url}): {str(e)}'
      raise exceptions.InferenceRuntimeError(
          error_msg, original=e
      ) from e

  def infer(
      self, batch_prompts: Sequence[str], **kwargs
  ) -> Iterator[Sequence[core_types.ScoredOutput]]:
    """Runs inference on a list of prompts via OpenAI's API.

    Args:
      batch_prompts: A list of string prompts.
      **kwargs: Additional generation params (temperature, top_p, etc.)

    Yields:
      Lists of ScoredOutputs.
    """
    merged_kwargs = self.merge_kwargs(kwargs)

    config = {}

    temp = merged_kwargs.get('temperature', self.temperature)
    if temp is not None:
      config['temperature'] = temp
    if 'max_output_tokens' in merged_kwargs:
      config['max_output_tokens'] = merged_kwargs['max_output_tokens']
    if 'top_p' in merged_kwargs:
      config['top_p'] = merged_kwargs['top_p']

    for key in [
        'frequency_penalty',
        'presence_penalty',
        'seed',
        'stop',
        'logprobs',
        'top_logprobs',
        'reasoning_effort',
        'reasoning',
        'response_format',
    ]:
      if key in merged_kwargs:
        config[key] = merged_kwargs[key]

    # Use parallel processing for batches larger than 1
    if len(batch_prompts) > 1 and self.max_workers > 1:
      with concurrent.futures.ThreadPoolExecutor(
          max_workers=min(self.max_workers, len(batch_prompts))
      ) as executor:
        future_to_index = {
            executor.submit(
                self._process_single_prompt, prompt, config.copy()
            ): i
            for i, prompt in enumerate(batch_prompts)
        }

        results: list[core_types.ScoredOutput | None] = [None] * len(
            batch_prompts
        )
        for future in concurrent.futures.as_completed(future_to_index):
          index = future_to_index[future]
          try:
            results[index] = future.result()
          except Exception as e:
            raise exceptions.InferenceRuntimeError(
                f'Parallel inference error: {str(e)}', original=e
            ) from e

        for result in results:
          if result is None:
            raise exceptions.InferenceRuntimeError(
                'Failed to process one or more prompts'
            )
          yield [result]
    else:
      # Sequential processing for single prompt or worker
      for prompt in batch_prompts:
        result = self._process_single_prompt(prompt, config.copy())
        yield [result]  # pylint: disable=duplicate-code