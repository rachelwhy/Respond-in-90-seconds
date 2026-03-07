import os
import time
import json
import csv
import sys
from tabulate import tabulate

# 将项目根目录添加到 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from ai_core.document_loaders import loader
from ai_core.core_engine import engine

# 配置
TEST_ROOT = "测试集"
OUTPUT_FILE = "test_results.csv"


def get_instruction_for_file(file_path):
    """返回统一的通用指令"""
    return "请提取这份文档中的重要信息，根据文档内容自然组织输出"


# 支持的扩展名与解析函数的映射
EXTENSION_MAP = {
    '.docx': loader.load_docx,
    '.doc': loader.load_docx,
    '.xlsx': loader.load_excel,
    '.xls': loader.load_excel,
    '.txt': lambda p: open(p, 'r', encoding='utf-8').read(),
    '.md': lambda p: open(p, 'r', encoding='utf-8').read(),
}


def get_file_extension(filename):
    """获取文件扩展名（小写）"""
    return os.path.splitext(filename)[1].lower()


def safe_json_dumps(obj):
    """安全地将对象转换为JSON字符串"""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except:
        return json.dumps({"error": "无法序列化"}, ensure_ascii=False)


def process_file(file_path):
    """处理单个文件：解析 + 抽取，返回结果和耗时"""
    ext = get_file_extension(file_path)
    if ext not in EXTENSION_MAP:
        return None, None, f"不支持的文件类型: {ext}"

    try:
        # 1. 解析文档
        parse_func = EXTENSION_MAP[ext]
        text = parse_func(file_path)

        # 2. 获取指令
        instruction = get_instruction_for_file(file_path)

        # 3. 抽取信息
        start = time.time()
        result = engine.process(text, instruction)
        elapsed = time.time() - start

        return result, elapsed, None
    except Exception as e:
        return None, None, str(e)


def main():
    results = []
    total_files = 0
    success_files = 0
    time_records = []

    if not os.path.exists(TEST_ROOT):
        print(f"错误：测试集文件夹 '{TEST_ROOT}' 不存在！")
        return

    for root, dirs, files in os.walk(TEST_ROOT):
        for file in files:
            file_path = os.path.join(root, file)
            print(f"正在处理: {file_path}")

            result, elapsed, error = process_file(file_path)
            total_files += 1

            status = "✅成功" if not error else "❌失败"
            filename = os.path.basename(file_path)

            record = {
                "file": file_path,
                "status": "success" if not error else "failed",
                "elapsed": round(elapsed, 2) if elapsed else None,
                "result": safe_json_dumps(result) if result else None,
                "error": error
            }
            results.append(record)

            if not error:
                success_files += 1
                time_records.append(elapsed)
                print(f"  {status} 耗时 {elapsed:.2f}秒")
                if result and result.get("data"):
                    data_keys = list(result["data"].keys())
                    print(f"     抽取字段: {data_keys}")
            else:
                print(f"  {status} {error}")

    # 将结果写入 CSV 文件
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=["file", "status", "elapsed", "result", "error"])
        writer.writeheader()
        writer.writerows(results)

    # 计算统计信息
    fail_files = total_files - success_files
    success_rate = success_files / total_files * 100 if total_files > 0 else 0

    # 时间统计
    if time_records:
        avg_time = sum(time_records) / len(time_records)
        max_time = max(time_records)
        min_time = min(time_records)
    else:
        avg_time = max_time = min_time = 0

    # 显示详细统计
    print("\n" + "=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)

    # 创建汇总表格
    summary_data = [
        ["总文件数", total_files],
        ["✅ 成功", f"{success_files} 个"],
        ["❌ 失败", f"{fail_files} 个"],
        ["📈 成功率", f"{success_rate:.1f}%"],
        ["⏱️ 平均耗时", f"{avg_time:.2f} 秒"],
        ["⚡ 最快", f"{min_time:.2f} 秒"],
        ["🐢 最慢", f"{max_time:.2f} 秒"],
    ]

    print(tabulate(summary_data, headers=["指标", "数值"], tablefmt="grid"))

    # 按耗时排序显示成功文件
    if time_records:
        print("\n⏱️ 文件处理耗时排名（按耗时从低到高）")
        print("-" * 50)

        # 提取成功文件及其耗时
        success_details = []
        for r in results:
            if r["status"] == "success":
                success_details.append({
                    "file": os.path.basename(r["file"]),
                    "elapsed": r["elapsed"]
                })

        # 按耗时排序
        success_details.sort(key=lambda x: x["elapsed"])

        # 显示排名
        rank_data = []
        for i, item in enumerate(success_details, 1):
            rank_data.append([i, item["file"], f"{item['elapsed']:.2f}秒"])

        print(tabulate(rank_data, headers=["排名", "文件名", "耗时"], tablefmt="simple"))

    # 显示失败文件
    if fail_files > 0:
        print("\n❌ 失败文件列表")
        print("-" * 50)
        fail_data = []
        for r in results:
            if r["status"] == "failed":
                fail_data.append([os.path.basename(r["file"]), r["error"]])

        print(tabulate(fail_data, headers=["文件名", "错误信息"], tablefmt="simple"))

    print("\n" + "=" * 60)
    print(f"📁 详细结果已保存到: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()