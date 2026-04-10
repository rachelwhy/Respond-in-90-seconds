import json
import os
from pathlib import Path


def validate_results(output_base: Path):
    """验证测试结果"""
    stats = {
        'total_files': 0,
        'success_files': 0,
        'failed_files': 0,
        'total_records': 0,
        'empty_files': []
    }

    for json_file in output_base.glob("**/*_result.json"):
        stats['total_files'] += 1

        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            records = data.get('records', [])
            if records and len(records) > 0:
                stats['success_files'] += 1
                stats['total_records'] += len(records)
            else:
                stats['failed_files'] += 1
                stats['empty_files'].append(str(json_file))

        except Exception as e:
            print(f"[ERROR] 验证文件失败 {json_file}: {e}")
            stats['failed_files'] += 1

    return stats


if __name__ == "__main__":
    output_base = Path("test/json_results")
    stats = validate_results(output_base)

    print("\n" + "="*50)
    print("测试结果验证报告")
    print("="*50)
    print(f"总文件数: {stats['total_files']}")
    print(f"成功文件数: {stats['success_files']}")
    print(f"失败文件数: {stats['failed_files']}")
    print(f"总提取记录数: {stats['total_records']}")

    if stats['empty_files']:
        print(f"\n空记录文件 ({len(stats['empty_files'])}个):")
        for file in stats['empty_files'][:5]:  # 只显示前5个
            print(f"  - {file}")
        if len(stats['empty_files']) > 5:
            print(f"  ... 还有 {len(stats['empty_files']) - 5} 个文件")

    # 成功标准检查
    if stats['total_files'] == 16 and stats['success_files'] > 12:
        print("\n✅ 测试通过: 大部分文件成功提取数据")
    elif stats['total_files'] > 0 and stats['success_files'] > stats['total_files'] * 0.7:
        print(f"\n⚠️ 部分通过: {stats['success_files']}/{stats['total_files']} 个文件成功提取数据")
    else:
        print("\n❌ 测试失败: 需要进一步排查")