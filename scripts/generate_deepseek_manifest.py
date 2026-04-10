#!/usr/bin/env python3
"""
生成DeepSeek API全面测试任务清单
扫描test/inputs/目录，为所有文件创建测试任务
"""

import json
from pathlib import Path
from datetime import datetime

# 通用模板路径
GENERIC_EXCEL_TEMPLATE = "data/template/generic_template.xlsx"
GENERIC_WORD_TEMPLATE = "data/template/generic_template.docx"  # 可能不存在


def scan_test_files(base_dir: Path = Path("test/inputs")):
    """扫描test/inputs/目录，生成任务清单

    Args:
        base_dir: 测试输入文件根目录

    Returns:
        List[dict]: 任务列表
    """
    tasks = []
    task_counter = 1

    # 1. 扫描Excel文件
    excel_dir = base_dir / "Excel"
    if excel_dir.exists():
        for excel_file in excel_dir.glob("*.xlsx"):
            tasks.append({
                "task_id": f"excel_{task_counter:03d}_{excel_file.stem[:20]}",
                "template_path": "data/template/generic_template.xlsx",  # 使用通用Excel模板
                "input_dir": str(excel_file.parent),
                "output_dir": f"test/results/deepseek/excel_{task_counter:03d}",
                "file_type": "excel",
                "description": f"Excel文件提取: {excel_file.name}",
                "mode": "table_records"  # 表格记录模式
            })
            task_counter += 1

    # 2. 扫描Word文件
    word_dir = base_dir / "word"
    if word_dir.exists():
        for word_file in word_dir.glob("*.docx"):
            tasks.append({
                "task_id": f"word_{task_counter:03d}_{word_file.stem[:20]}",
                "template_path": "data/template/generic_template.xlsx",  # 使用通用Excel模板
                "input_dir": str(word_file.parent),
                "output_dir": f"test/results/deepseek/word_{task_counter:03d}",
                "file_type": "word",
                "description": f"Word文件提取: {word_file.name}",
                "mode": "table_records"
            })
            task_counter += 1

    # 3. 扫描Markdown文件
    md_dir = base_dir / "md"
    if md_dir.exists():
        for md_file in md_dir.glob("*.md"):
            tasks.append({
                "task_id": f"md_{task_counter:03d}_{md_file.stem[:20]}",
                "template_path": "auto_generate",
                "input_dir": str(md_file.parent),
                "output_dir": f"test/results/deepseek/md_{task_counter:03d}",
                "file_type": "markdown",
                "description": f"Markdown文件提取: {md_file.name}",
                "mode": "table_records"
            })
            task_counter += 1

    # 4. 扫描文本文件
    txt_dir = base_dir / "txt"
    if txt_dir.exists():
        for txt_file in txt_dir.glob("*.txt"):
            tasks.append({
                "task_id": f"txt_{task_counter:03d}_{txt_file.stem[:20]}",
                "template_path": "auto_generate",
                "input_dir": str(txt_file.parent),
                "output_dir": f"test/results/deepseek/txt_{task_counter:03d}",
                "file_type": "text",
                "description": f"文本文件提取: {txt_file.name}",
                "mode": "table_records"
            })
            task_counter += 1

    # 5. 扫描包含模板的完整任务包
    template_dir = base_dir / "包含模板文件"
    if template_dir.exists():
        for task_package in template_dir.iterdir():
            if task_package.is_dir():
                # 查找模板文件和输入文件
                template_files = list(task_package.glob("*模板*"))
                template_files.extend(task_package.glob("*.docx"))
                template_files.extend(task_package.glob("*.xlsx"))

                input_files = list(task_package.glob("*数据*"))
                input_files.extend(task_package.glob("*.xlsx"))
                input_files.extend(task_package.glob("*.txt"))
                input_files.extend(task_package.glob("*.docx"))

                # 移除可能的重复
                template_files = [f for f in template_files if f not in input_files]

                if template_files and input_files:
                    # 使用第一个模板文件和第一个输入文件
                    template_path = str(template_files[0])
                    input_path = str(input_files[0])

                    tasks.append({
                        "task_id": f"template_{task_counter:03d}_{task_package.name[:15]}",
                        "template_path": template_path,
                        "input_dir": str(task_package),
                        "output_dir": f"test/results/deepseek/template_{task_counter:03d}",
                        "file_type": "mixed",
                        "description": f"模板任务包: {task_package.name}",
                        "mode": "table_records",
                        "has_template": True
                    })
                    task_counter += 1

    return tasks


def generate_manifest(output_path: Path = Path("test/manifests/deepseek_full_test.json")):
    """生成任务清单并保存到文件

    Args:
        output_path: 输出文件路径
    """
    print("开始扫描测试文件...")
    tasks = scan_test_files()

    if not tasks:
        print("未找到任何测试文件！")
        return

    print(f"找到 {len(tasks)} 个测试文件")

    # 创建清单结构
    manifest = {
        "name": "DeepSeek API全面测试清单",
        "description": "测试集中所有文件的DeepSeek API测试",
        "timestamp": datetime.now().isoformat(),
        "model_type": "deepseek",
        "api_key_provided": True,
        "tasks": tasks
    }

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 保存为JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"任务清单已生成: {output_path}")
    print(f"包含 {len(tasks)} 个任务:")

    # 按类型统计
    type_stats = {}
    for task in tasks:
        file_type = task.get('file_type', 'unknown')
        type_stats[file_type] = type_stats.get(file_type, 0) + 1

    for file_type, count in type_stats.items():
        print(f"  {file_type}: {count} 个文件")

    return output_path


if __name__ == '__main__':
    # 生成清单
    manifest_path = generate_manifest()

    if manifest_path:
        print(f"\n运行批量测试命令:")
        print(f"python scripts/run_batch.py \\")
        print(f"  --manifest {manifest_path} \\")
        print(f"  --main-script main.py \\")
        print(f"  --validate \\")
        print(f"  --validation-mode fieldwise \\")
        print(f"  --collect-metrics \\")
        print(f"  --output-report test/reports/deepseek_benchmark_report.json")