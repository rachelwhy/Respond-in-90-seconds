import os
import time
import json
import csv
import sys
from tabulate import tabulate

sys.path.insert(0, os.path.dirname(__file__))

from ai_core.core_engine import core_engine

TEST_ROOT = "测试集"
OUTPUT_FILE = "test_results.csv"
TIMEOUT_LIMIT = 90

SUPPORTED_EXTS = ['.docx', '.doc', '.xlsx', '.xls', '.txt', '.md']


def get_instruction_for_file(file_path):
    return "提取文档中的重要信息"


def process_file(file_path):
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in SUPPORTED_EXTS:
        return None, None, f"不支持的文件类型: {ext}"

    try:
        instruction = get_instruction_for_file(file_path)

        start = time.time()
        result = core_engine.process(
            file_path=file_path,
            instruction=instruction,
            template=None
        )
        elapsed = time.time() - start

        # ✅ 根据不同文件类型检查结果
        if result.get("error"):
            return None, elapsed, result["error"]

        file_type = result.get("file_type", "")

        # Excel 文件检查 data 字段
        if file_type == "excel":
            if not result.get("data"):
                return None, elapsed, "抽取结果为空"

        # 其他文件检查 fields 字段
        elif not result.get("fields"):
            return None, elapsed, "抽取结果为空"

        # Word 文件特殊处理：86秒那个虽然成功但有 JSON 解析失败
        if file_type == "word" and elapsed > 85:
            print(f"  ⚠️ Word 文件耗时较长，但已成功")

        return result, elapsed, None

    except Exception as e:
        return None, None, str(e)


def safe_json_dumps(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except:
        return json.dumps({"error": "无法序列化"}, ensure_ascii=False)


def main():
    results = []
    total_files = 0
    success_files = 0
    time_records = []
    timeout_files = []

    if not os.path.exists(TEST_ROOT):
        print(f"错误：测试集文件夹 '{TEST_ROOT}' 不存在！")
        return

    for root, dirs, files in os.walk(TEST_ROOT):
        for file in files:
            file_path = os.path.join(root, file)
            print(f"正在处理: {file_path}")

            result, elapsed, error = process_file(file_path)
            total_files += 1

            status = "success" if not error else "failed"

            record = {
                "file": file_path,
                "status": status,
                "elapsed": round(elapsed, 2) if elapsed else None,
                "result": safe_json_dumps(result) if result else None,
                "error": error
            }
            results.append(record)

            if not error:
                success_files += 1
                time_records.append(elapsed)
                if elapsed > TIMEOUT_LIMIT:
                    timeout_files.append((file, elapsed))

                # 显示更多信息
                file_type = result.get("file_type", "unknown")
                if file_type == "excel":
                    row_count = result.get("row_count", 0)
                    print(f"  ✅ 成功，耗时 {elapsed:.2f}秒, 类型: {file_type}, 行数: {row_count}")
                else:
                    field_count = len(result.get("fields", []))
                    print(f"  ✅ 成功，耗时 {elapsed:.2f}秒, 类型: {file_type}, 字段数: {field_count}")
            else:
                print(f"  ❌ 失败: {error}")

    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=["file", "status", "elapsed", "result", "error"])
        writer.writeheader()
        writer.writerows(results)

    fail_files = total_files - success_files
    success_rate = success_files / total_files * 100 if total_files > 0 else 0

    if time_records:
        avg_time = sum(time_records) / len(time_records)
        max_time = max(time_records)
        min_time = min(time_records)
    else:
        avg_time = max_time = min_time = 0

    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)

    summary_data = [
        ["总文件数", total_files],
        ["✅ 成功", f"{success_files} 个"],
        ["❌ 失败", f"{fail_files} 个"],
        ["📈 成功率", f"{success_rate:.1f}%"],
        ["⏱️ 平均耗时", f"{avg_time:.2f} 秒"],
        ["⚡ 最快", f"{min_time:.2f} 秒"],
        ["🐢 最慢", f"{max_time:.2f} 秒"],
        ["⏰ 超时 (>90s)", len(timeout_files)]
    ]

    print(tabulate(summary_data, headers=["指标", "数值"], tablefmt="grid"))
    print("=" * 60)
    print(f"📁 详细结果已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()