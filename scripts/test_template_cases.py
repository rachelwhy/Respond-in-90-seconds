#!/usr/bin/env python3
"""
命令行模板测试脚本

测试位于 `命令行端测试/任务输入/包含模板文件/` 目录下的测试案例。
模拟网页端API的实际使用情况，但跳过HTTP的传入和传出部分，其他部分完全一致。

要求：
1. 使用本地Ollama模型（默认）
2. 产生的Excel、Word、JSON文件输出到 `命令行端测试/任务输出/` 文件夹
3. 输出文件命名和格式与API调用结果完全一致
4. 作为未来所有新脚本的模板

使用方法：
python scripts/test_template_cases.py [选项]

示例：
python scripts/test_template_cases.py --model-type ollama --llm-mode full
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.api.direct_extractor import direct_extract
from src.core.writers import fill_excel_table, fill_excel_vertical, fill_word_table


# 配置日志
def setup_logging(log_dir: Path, quiet: bool = False) -> logging.Logger:
    """设置结构化日志记录系统"""
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"template_test_{timestamp}.log"

    logger = logging.getLogger("template_test")
    logger.setLevel(logging.DEBUG)

    # 文件处理器（详细日志）
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 控制台处理器（除非安静模式）
    if not quiet:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger


def discover_test_cases(input_dir: Path) -> List[Dict[str, Any]]:
    """自动发现测试案例

    遍历输入目录，识别包含模板文件和输入文件的子目录。
    每个测试案例包含：
    - name: 案例名称（目录名）
    - case_dir: 案例目录路径
    - template_path: 模板文件路径
    - input_files: 输入文件列表（排除模板文件和用户要求文件）
    - instruction: 用户要求文本（如果存在）
    """
    test_cases = []

    if not input_dir.exists():
        return test_cases

    for item in input_dir.iterdir():
        if not item.is_dir():
            continue

        case_dir = item
        case_name = item.name

        # 查找模板文件（包含"模板"或"template"关键词）
        template_files = []
        for suffix in [".xlsx", ".xls", ".docx", ".doc"]:
            template_files.extend(list(case_dir.glob(f"*模板*{suffix}")))
            template_files.extend(list(case_dir.glob(f"*template*{suffix}")))

        if not template_files:
            continue

        template_path = template_files[0]

        # 查找输入文件（排除模板文件和用户要求文件）
        input_files = []
        for file_path in case_dir.iterdir():
            if file_path.is_file():
                # 跳过模板文件
                if file_path == template_path:
                    continue
                # 跳过用户要求文件
                if "用户要求" in file_path.name or "用户要求.txt" == file_path.name:
                    continue
                input_files.append(file_path)

        # 读取用户要求（如果存在）
        instruction = ""
        user_req_file = case_dir / "用户要求.txt"
        if user_req_file.exists():
            try:
                instruction = user_req_file.read_text(encoding='utf-8').strip()
            except Exception:
                instruction = ""

        test_cases.append({
            "name": case_name,
            "case_dir": case_dir,
            "template_path": template_path,
            "input_files": input_files,
            "instruction": instruction
        })

    return test_cases


def create_input_bundle(input_files: List[Path], work_dir: Path) -> Path:
    """创建输入文件包（复制输入文件到工作目录）

    由于direct_extract需要输入目录，我们将输入文件复制到临时目录。
    返回临时目录路径。
    """
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    for src_file in input_files:
        dst_file = input_dir / src_file.name
        # 如果文件已经存在（同名），添加后缀
        counter = 1
        while dst_file.exists():
            stem = src_file.stem
            suffix = src_file.suffix
            dst_file = input_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        import shutil
        shutil.copy2(src_file, dst_file)

    return input_dir


def process_test_case(
    case_info: Dict[str, Any],
    output_dir: Path,
    model_type: str,
    llm_mode: str,
    total_timeout: int,
    max_chunks: int,
    quiet: bool,
    logger: logging.Logger
) -> Dict[str, Any]:
    """处理单个测试案例

    返回处理结果字典，包含成功/失败状态、处理时间、输出文件路径等。
    """
    case_name = case_info["name"]
    case_dir = case_info["case_dir"]
    template_path = case_info["template_path"]
    input_files = case_info["input_files"]
    instruction = case_info["instruction"]

    logger.info(f"开始处理测试案例: {case_name}")
    logger.debug(f"模板文件: {template_path}")
    logger.debug(f"输入文件数: {len(input_files)}")
    logger.debug(f"抽取指令: {instruction[:100] if instruction else '无'}")

    start_time = time.time()
    result = {
        "case_name": case_name,
        "status": "unknown",
        "error": None,
        "processing_time_seconds": 0,
        "record_count": 0,
        "output_files": {},
        "metadata": {}
    }

    try:
        # 创建案例输出目录
        case_output_dir = output_dir / case_name
        case_output_dir.mkdir(parents=True, exist_ok=True)

        # 创建工作目录（临时文件）
        work_dir = output_dir / f"work_{case_name}_{int(start_time)}"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 创建输入文件包
        input_dir = create_input_bundle(input_files, work_dir)

        # 确定输出basename（使用第一个输入文件的名称，不含扩展名）
        basename = ""
        if input_files:
            basename = input_files[0].stem

        # 调用直接抽取函数
        logger.info(f"调用直接抽取函数...")

        extract_result = direct_extract(
            template_path=str(template_path),
            input_dir=str(input_dir),
            model_type=model_type,
            instruction=instruction if instruction else None,
            llm_mode=llm_mode,
            enable_unit_aware=True,
            work_dir=work_dir,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )

        # 处理records，保持原始结构但提供便利访问
        raw_records = extract_result.get("records", [])
        metadata = extract_result.get("metadata", {})

        # 调试：打印raw_records结构
        logger.debug(f"raw_records类型: {type(raw_records)}")
        if isinstance(raw_records, dict):
            logger.debug(f"raw_records keys: {list(raw_records.keys())}")
            if "records" in raw_records:
                logger.debug(f"raw_records['records']类型: {type(raw_records['records'])}")
                if isinstance(raw_records['records'], list):
                    logger.debug(f"raw_records['records']长度: {len(raw_records['records'])}")
                    if raw_records['records']:
                        logger.debug(f"第一条记录: {raw_records['records'][0]}")

        # 提取records列表用于writer函数（期望列表）
        if isinstance(raw_records, dict) and "records" in raw_records:
            records_for_writers = raw_records["records"] if isinstance(raw_records["records"], list) else []
        elif isinstance(raw_records, list):
            records_for_writers = raw_records
        else:
            records_for_writers = []

        logger.debug(f"records_for_writers长度: {len(records_for_writers)}")
        if records_for_writers:
            logger.debug(f"records_for_writers第一条: {records_for_writers[0]}")

        # 保持原始records用于JSON输出
        records = raw_records

        # 计算有效记录数量（排除空字典）
        def count_valid_records(recs):
            if isinstance(recs, dict) and "records" in recs:
                if isinstance(recs["records"], list):
                    # 统计非空字典
                    valid_count = 0
                    for item in recs["records"]:
                        if isinstance(item, dict) and item:
                            valid_count += 1
                    return valid_count
                return 0
            elif isinstance(recs, list):
                # 统计非空字典
                valid_count = 0
                for item in recs:
                    if isinstance(item, dict) and item:
                        valid_count += 1
                return valid_count
            return 0

        record_count = count_valid_records(records)
        total_count = 0  # 总记录数（包括空记录）
        if isinstance(records, dict) and "records" in records and isinstance(records["records"], list):
            total_count = len(records["records"])
        elif isinstance(records, list):
            total_count = len(records)

        result["record_count"] = record_count
        result["total_record_count"] = total_count
        result["metadata"] = metadata

        if total_count > 0 and record_count == 0:
            logger.info(f"抽取完成，获取 {total_count} 条记录（全部为空，无有效数据）")
        else:
            logger.info(f"抽取完成，获取 {record_count} 条有效记录（共 {total_count} 条）")

        if record_count == 0:
            logger.warning(f"未提取到有效记录（共 {total_count} 条记录，全部为空）")
            result["status"] = "success_no_records"
        else:
            # 生成输出文件
            logger.info(f"生成输出文件...")
            output_files = generate_output_files(
                records=records,  # 原始records（可能是字典或列表）
                records_for_writers=records_for_writers,  # 用于writer的列表
                template_path=template_path,
                basename=basename,
                output_dir=case_output_dir,
                record_count=record_count,  # 有效记录数
                logger=logger
            )

            result["output_files"] = output_files
            result["status"] = "success"

        # 清理工作目录（可选）
        import shutil
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            logger.debug(f"清理工作目录失败: {e}")

    except Exception as e:
        logger.error(f"处理测试案例失败: {case_name}", exc_info=True)
        result["status"] = "failed"
        result["error"] = str(e)

    result["processing_time_seconds"] = time.time() - start_time
    logger.info(f"处理完成: {case_name}，状态: {result['status']}，耗时: {result['processing_time_seconds']:.2f}秒")

    return result


def generate_output_files(
    records: Any,
    records_for_writers: List[Dict],
    template_path: Path,
    basename: str,
    output_dir: Path,
    record_count: int,
    logger: logging.Logger
) -> Dict[str, str]:
    """生成与API兼容的输出文件

    根据模板类型和记录数量，生成合适的输出文件格式。

    Args:
        records: 原始records数据（可能是字典或列表）
        records_for_writers: 用于writer函数的记录列表
        template_path: 模板文件路径
        basename: 输出文件基础名称
        output_dir: 输出目录
        logger: 日志记录器

    返回输出文件路径字典。
    """
    output_files = {}

    if not basename:
        basename = "result"

    # 1. 生成JSON文件（保持原始结构）
    json_path = output_dir / f"{basename}_result.json"
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            # 如果records已经是字典，直接写入；否则包装为{"records": records}
            if isinstance(records, dict):
                json.dump(records, f, ensure_ascii=False, indent=2)
            else:
                json.dump({"records": records}, f, ensure_ascii=False, indent=2)
        output_files["json"] = str(json_path)
        logger.debug(f"生成JSON文件: {json_path}")
    except Exception as e:
        logger.error(f"生成JSON文件失败: {e}")

    # 2. 生成Excel文件（如果模板是Excel格式）
    if template_path.suffix.lower() in ['.xlsx', '.xls']:
        excel_path = output_dir / f"{basename}_result.xlsx"
        try:
            if not records_for_writers:
                logger.warning(f"无记录可写入Excel文件")
            elif len(records_for_writers) > 1:
                fill_excel_table(str(template_path), str(excel_path), records_for_writers)
            else:
                fill_excel_vertical(str(template_path), str(excel_path), records_for_writers[0])
            output_files["excel"] = str(excel_path)
            logger.debug(f"生成Excel文件: {excel_path}")
        except Exception as e:
            logger.error(f"生成Excel文件失败: {e}")
            import traceback
            logger.debug(f"Excel生成错误详情: {traceback.format_exc()}")

    # 3. 生成Word文件（如果模板是Word格式）
    elif template_path.suffix.lower() in ['.docx', '.doc']:
        word_path = output_dir / f"{basename}_result.docx"
        try:
            if not records_for_writers:
                logger.warning(f"无记录可写入Word文件")
            else:
                fill_word_table(str(template_path), str(word_path), records_for_writers)
            output_files["word"] = str(word_path)
            logger.debug(f"生成Word文件: {word_path}")
        except Exception as e:
            logger.error(f"生成Word文件失败: {e}")
            import traceback
            logger.debug(f"Word生成错误详情: {traceback.format_exc()}")

    # 4. 生成处理报告文件
    report_path = output_dir / f"{basename}_result_report.json"
    try:
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "record_count": record_count,  # 使用传入的有效记录数
            "output_files": list(output_files.keys())
        }
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        output_files["report"] = str(report_path)
        logger.debug(f"生成报告文件: {report_path}")
    except Exception as e:
        logger.error(f"生成报告文件失败: {e}")

    return output_files


def print_summary(results: List[Dict[str, Any]], logger: logging.Logger) -> None:
    """打印处理结果汇总"""
    total = len(results)
    success = sum(1 for r in results if r["status"] == "success")
    success_no_records = sum(1 for r in results if r["status"] == "success_no_records")
    failed = sum(1 for r in results if r["status"] == "failed")

    total_time = sum(r.get("processing_time_seconds", 0) for r in results)
    total_records = sum(r.get("record_count", 0) for r in results)

    logger.info("=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)
    logger.info(f"测试案例总数: {total}")
    logger.info(f"成功（有记录）: {success}")
    logger.info(f"成功（无记录）: {success_no_records}")
    logger.info(f"失败: {failed}")
    logger.info(f"总处理时间: {total_time:.2f}秒")
    logger.info(f"总记录数: {total_records}")

    if failed > 0:
        logger.info("\n失败案例详情:")
        for result in results:
            if result["status"] == "failed":
                logger.info(f"  - {result['case_name']}: {result.get('error', '未知错误')}")

    # 打印输出文件信息
    logger.info("\n输出文件位置: 命令行端测试/任务输出/<案例名称>/")
    for result in results:
        if result["status"] == "success" and result.get("output_files"):
            logger.info(f"\n案例: {result['case_name']}")
            for file_type, file_path in result["output_files"].items():
                logger.info(f"  - {file_type}: {Path(file_path).name}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="命令行模板测试脚本 - 测试包含模板文件的测试案例",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认设置运行所有测试案例
  python scripts/test_template_cases.py

  # 使用Ollama模型，完整AI抽取模式
  python scripts/test_template_cases.py --model-type ollama --llm-mode full

  # 纯规则抽取模式（无AI）
  python scripts/test_template_cases.py --llm-mode off

  # 指定超时和分块数量
  python scripts/test_template_cases.py --total-timeout 180 --max-chunks 100

  # 安静模式（仅输出错误）
  python scripts/test_template_cases.py --quiet
        """
    )

    # 模型相关参数
    parser.add_argument("--model-type", type=str, default="ollama",
                       choices=["ollama", "deepseek", "openai", "qwen"],
                       help="模型类型，默认: ollama")

    parser.add_argument("--llm-mode", type=str, default="full",
                       choices=["full", "supplement", "off"],
                       help="LLM抽取模式: full(始终全文抽取,默认), supplement(仅补充缺失字段), off(仅规则抽取)")

    # 超时和性能参数
    parser.add_argument("--total-timeout", type=int, default=110,
                       help="总超时时间（秒），默认: 110")

    parser.add_argument("--max-chunks", type=int, default=50,
                       help="最大语义分块数量，默认: 50")

    parser.add_argument("--quiet", action="store_true",
                       help="安静模式，禁用控制台进度输出")

    # 输入输出路径参数
    parser.add_argument("--input-dir", type=str,
                       default="命令行端测试/任务输入/包含模板文件",
                       help="测试案例输入目录，默认: 命令行端测试/任务输入/包含模板文件")

    parser.add_argument("--output-dir", type=str,
                       default="命令行端测试/任务输出",
                       help="测试输出目录，默认: 命令行端测试/任务输出")

    parser.add_argument("--log-dir", type=str,
                       default="test/results/logs",
                       help="日志文件目录，默认: test/results/logs")

    args = parser.parse_args()

    # 设置日志
    logger = setup_logging(Path(args.log_dir), args.quiet)

    # 记录参数
    logger.info("命令行模板测试脚本启动")
    logger.info(f"输入目录: {args.input_dir}")
    logger.info(f"输出目录: {args.output_dir}")
    logger.info(f"模型类型: {args.model_type}")
    logger.info(f"LLM模式: {args.llm_mode}")
    logger.info(f"总超时: {args.total_timeout}秒")
    logger.info(f"最大分块数: {args.max_chunks}")

    # 检查输入目录
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        logger.error(f"输入目录不存在: {input_dir}")
        return 1

    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 发现测试案例
    logger.info("发现测试案例...")
    test_cases = discover_test_cases(input_dir)

    if not test_cases:
        logger.error(f"未发现测试案例，请检查输入目录: {input_dir}")
        return 1

    logger.info(f"发现 {len(test_cases)} 个测试案例:")
    for case in test_cases:
        logger.info(f"  - {case['name']} (模板: {case['template_path'].name}, 输入文件: {len(case['input_files'])})")

    # 处理每个测试案例
    results = []
    for i, case in enumerate(test_cases, 1):
        logger.info(f"\n处理案例 {i}/{len(test_cases)}: {case['name']}")

        result = process_test_case(
            case_info=case,
            output_dir=output_dir,
            model_type=args.model_type,
            llm_mode=args.llm_mode,
            total_timeout=args.total_timeout,
            max_chunks=args.max_chunks,
            quiet=args.quiet,
            logger=logger
        )

        results.append(result)

    # 打印汇总结果
    print_summary(results, logger)

    # 保存汇总结果到文件
    summary_file = output_dir / "test_summary.json"
    try:
        summary_data = {
            "timestamp": datetime.now().isoformat(),
            "args": vars(args),
            "results": results
        }
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        logger.info(f"详细结果已保存到: {summary_file}")
    except Exception as e:
        logger.error(f"保存汇总结果失败: {e}")

    # 返回退出码（如果有失败案例）
    failed_count = sum(1 for r in results if r["status"] == "failed")
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n用户中断执行")
        sys.exit(130)
    except Exception as e:
        print(f"脚本执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)