"""抽取完成后按 profile 写回 Excel/Word 及记录去重编排（CLI 与 API 共用）。"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.extraction_result_harmonizer import records_from_final_data
from src.core.record_dedup import dedup_records
from src.core.writers import create_excel_from_records, fill_excel_table, fill_excel_vertical, fill_word_table


def _build_word_multi_groups(records: list, final_data: Any, profile: dict, logger: logging.Logger) -> list:
    def _apply_fixed_values(rows: list, fixed: dict) -> list:
        if not fixed:
            return rows
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rr = dict(row)
            for k, v in fixed.items():
                if str(v).strip():
                    rr[k] = v
            out.append(rr)
        return out

    def _dedup_group_records(rows: list, spec: dict) -> list:
        if not rows:
            return rows
        if not isinstance(spec, dict):
            spec = {}

        dedup_fields = spec.get("dedup_key_fields")
        deduped, _, _ = dedup_records(rows, preferred_fields=dedup_fields if isinstance(dedup_fields, list) else None)
        return deduped

    def _cap_rows_by_template_capacity(rows: list, spec: dict) -> list:
        if not isinstance(spec, dict):
            return rows
        cap = spec.get("max_rows")
        try:
            cap_n = int(cap)
        except Exception:
            return rows
        if cap_n <= 0:
            return rows
        return list(rows)[:cap_n]

    table_specs = profile.get("table_specs", [])
    pre_groups = final_data.get("_table_groups") if isinstance(final_data, dict) else None
    if isinstance(pre_groups, list) and pre_groups:
        spec_by_index = {
            int(s.get("table_index", i)): s for i, s in enumerate(table_specs) if isinstance(s, dict)
        }
        table_groups = [
            {"table_index": int(g.get("table_index", 0)), "records": g.get("records", [])} for g in pre_groups
        ]
        fixed_by_index = {
            int(s.get("table_index", i)): dict(s.get("fixed_values") or {})
            for i, s in enumerate(table_specs)
            if isinstance(s, dict)
        }
        for g in table_groups:
            tid = int(g.get("table_index", 0))
            rows = _apply_fixed_values(g.get("records", []), fixed_by_index.get(tid, {}))
            g["records"] = _dedup_group_records(rows, spec_by_index.get(tid, {}))
            g["records"] = _cap_rows_by_template_capacity(g["records"], spec_by_index.get(tid, {}))
        logger.info("使用并行抽取生成的 _table_groups（%s 组）填表", len(table_groups))
        return table_groups

    table_groups = []
    for i, spec in enumerate(table_specs):
        sp = spec if isinstance(spec, dict) else {}
        filter_field = str(sp.get("filter_field", "") or "").strip()
        filter_value = str(sp.get("filter_value", "") or "").strip()
        fixed_values = dict(sp.get("fixed_values") or {})
        table_idx = int(sp.get("table_index", i))
        if filter_field and filter_value:
            group_records = [
                r for r in records if isinstance(r, dict) and filter_value in str(r.get(filter_field, ""))
            ]
        else:
            group_records = list(records) if table_idx == 0 else []
        group_records = _apply_fixed_values(group_records, fixed_values)
        group_records = _dedup_group_records(group_records, sp)
        group_records = _cap_rows_by_template_capacity(group_records, sp)
        logger.info("表格%s（%s）: %s 条记录", table_idx + 1, filter_value, len(group_records))
        table_groups.append({"table_index": table_idx, "records": group_records})
    return table_groups


def write_template_outputs_cli(
    *,
    template_path: str,
    is_no_template: bool,
    is_generic_template: bool,
    final_data: Any,
    profile: dict,
    output_xlsx: str,
    output_docx: str,
    logger: logging.Logger,
) -> str:
    template_mode = profile.get("template_mode", "vertical")

    if not template_path or is_no_template:
        logger.info("无模板：动态创建Excel输出")
        records = records_from_final_data(final_data)
        if records:
            create_excel_from_records(output_xlsx, records)
            logger.info("动态Excel已生成: %s，共 %s 条记录", output_xlsx, len(records))
        else:
            logger.info("无有效记录，跳过Excel输出")
        return template_mode

    if is_generic_template:
        logger.info("通用模板：动态创建任务专属Excel（不受模板列限制）")
        records = records_from_final_data(final_data)
        create_excel_from_records(output_xlsx, records)
        logger.info("动态Excel已生成: %s，共 %s 条记录", output_xlsx, len(records))
        return template_mode

    if template_mode == "word_multi_table":
        records = final_data.get("records", []) if isinstance(final_data, dict) else (
            final_data if isinstance(final_data, list) else []
        )
        logger.info("Word多表格模式：共 %s 条记录，%s 个表格", len(records), len(profile.get("table_specs", [])))
        table_groups = _build_word_multi_groups(records, final_data, profile, logger)
        fill_payload = {"records": records, "_table_groups": table_groups}
        fill_word_table(
            template_path=template_path,
            output_path=output_docx,
            records=fill_payload,
            header_row=profile.get("header_row", 0),
            start_row=profile.get("start_row", 1),
        )
        return template_mode

    if template_mode == "vertical":
        fill_excel_vertical(template_path, output_xlsx, final_data)
    elif template_mode == "excel_table":
        fill_excel_table(
            template_path=template_path,
            output_path=output_xlsx,
            records=final_data,
            header_row=profile.get("header_row", 1),
            start_row=profile.get("start_row", 2),
        )
    elif template_mode == "word_table":
        fill_word_table(
            template_path=template_path,
            output_path=output_docx,
            records=final_data,
            table_index=profile.get("table_index", 0),
            header_row=profile.get("header_row", 0),
            start_row=profile.get("start_row", 1),
        )
    else:
        logger.warning("未知template_mode: %s，尝试按excel_table处理", template_mode)
        fill_excel_table(template_path=template_path, output_path=output_xlsx, records=final_data, header_row=1, start_row=2)
    return template_mode


def write_template_outputs_api(
    *,
    template_path: str,
    work_dir: Optional[Path],
    records: List[Dict[str, Any]],
    profile: Dict[str, Any],
    final_data: Any,
    logger: logging.Logger,
) -> Tuple[Optional[str], List[str]]:
    output_file: Optional[str] = None
    output_files: List[str] = []
    if work_dir is None:
        return output_file, output_files
    if not records:
        logger.info("无有效记录，跳过文件生成")
        return output_file, output_files

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        template_mode = profile.get("template_mode", "excel_table")
        ts = int(time.time())
        template_suffix = (Path(template_path).suffix or "").lower() if template_path else ""

        if template_path and Path(template_path).exists() and template_suffix in (".xlsx", ".xls", ".xlsm"):
            if template_mode == "vertical":
                output_path = work_dir / f"extracted_{ts}.xlsx"
                vertical_data = records[0] if records and isinstance(records[0], dict) else {}
                fill_excel_vertical(str(template_path), str(output_path), vertical_data)
                output_files.append(str(output_path))
            else:
                grouped: Dict[str, List[dict]] = {}
                ordered_keys: List[str] = []
                for row in records:
                    if not isinstance(row, dict):
                        continue
                    src = str(row.get("_source_file") or "").strip()
                    if not src:
                        src = "merged"
                    if src not in grouped:
                        grouped[src] = []
                        ordered_keys.append(src)
                    grouped[src].append({k: v for k, v in row.items() if not str(k).startswith("_")})
                if len(ordered_keys) > 1:
                    for idx, src in enumerate(ordered_keys, start=1):
                        src_stem = Path(src).stem or src
                        safe_src = re.sub(r"[^\w\-.]+", "_", src_stem)[:60] or f"group_{idx}"
                        output_path = work_dir / f"extracted_{ts}_{idx}_{safe_src}.xlsx"
                        fill_excel_table(
                            template_path=str(template_path),
                            output_path=str(output_path),
                            records=grouped.get(src, []),
                            header_row=int(profile.get("header_row", 1)),
                            start_row=int(profile.get("start_row", 2)),
                        )
                        output_files.append(str(output_path))
                else:
                    output_path = work_dir / f"extracted_{ts}.xlsx"
                    single_records = grouped[ordered_keys[0]] if ordered_keys else []
                    fill_excel_table(
                        template_path=str(template_path),
                        output_path=str(output_path),
                        records=single_records,
                        header_row=int(profile.get("header_row", 1)),
                        start_row=int(profile.get("start_row", 2)),
                    )
                    output_files.append(str(output_path))
            output_file = output_files[0] if output_files else None
            logger.info("按 Excel 模板写回成功: %s", output_file)
            return output_file, output_files

        if template_path and Path(template_path).exists() and template_suffix in (".docx", ".doc"):
            output_path = work_dir / f"extracted_{ts}.docx"
            if profile.get("template_mode") == "word_multi_table" and isinstance(final_data, dict) and final_data.get("_table_groups"):
                fill_payload: Dict[str, Any] = {"records": records, "_table_groups": final_data.get("_table_groups")}
            else:
                fill_payload = {"records": records}
            fill_word_table(
                template_path=str(template_path),
                output_path=str(output_path),
                records=fill_payload,
                table_index=int(profile.get("table_index", 0)),
                header_row=int(profile.get("header_row", 0)),
                start_row=int(profile.get("start_row", 1)),
            )
            output_file = str(output_path)
            output_files.append(str(output_path))
            logger.info("按 Word 模板写回成功: %s", output_file)
            return output_file, output_files

        output_path = work_dir / f"extracted_{ts}.xlsx"
        create_excel_from_records(str(output_path), records)
        output_file = str(output_path)
        output_files.append(str(output_path))
        logger.info("模板不可写回，改用动态 Excel: %s", output_file)
        return output_file, output_files
    except Exception as e:
        logger.warning("生成输出文件失败: %s", e)
        return output_file, output_files
