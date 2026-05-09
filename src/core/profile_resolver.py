from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.core.profile import (
    apply_instruction_runtime_hints,
    apply_word_multi_instruction_constraints,
    effective_instruction_text,
    generate_profile_from_document,
    generate_profile_from_template,
    generate_profile_smart,
)


def resolve_profile(
    *,
    template_path: str,
    instruction: Optional[str],
    document_text: str = "",
    logger: Optional[logging.Logger] = None,
) -> dict:
    """
    统一 profile 解析入口：
    1) 有模板文件
    2) 无模板但有指令
    3) 无模板无指令（文档自动分析）
    """
    if template_path and str(template_path).strip():
        path = Path(template_path)
        if path.suffix.lower() == ".json":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if path.exists():
            profile = generate_profile_from_template(
                template_path=template_path,
                use_llm=bool(instruction),
                mode="auto",
                user_description=instruction,
            )
            if profile and profile.get("fields"):
                profile = apply_instruction_runtime_hints(profile, instruction or "")
                if profile.get("template_mode") == "word_multi_table":
                    profile = apply_word_multi_instruction_constraints(
                        profile,
                        effective_instruction_text(instruction, profile),
                    )
                return profile

    if instruction and str(instruction).strip():
        profile = generate_profile_smart(
            template_path="",
            instruction=instruction,
            document_sample=(document_text[:3000] if document_text else ""),
        )
        if profile and profile.get("fields"):
            return apply_instruction_runtime_hints(profile, instruction or "")

    if document_text and str(document_text).strip():
        if logger is not None:
            logger.info("无模板无指令，启动文档自动分析...")
        profile = generate_profile_from_document(document_text)
        if profile and profile.get("fields"):
            return apply_instruction_runtime_hints(profile, instruction or "")

    from src.core.profile import _default_profile

    return _default_profile(template_path)
