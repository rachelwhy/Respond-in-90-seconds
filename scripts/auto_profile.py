import os
import json
import argparse
from pathlib import Path

from src.core.profile import generate_profile_from_template


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, help="模板路径，例如 data/template/template.xlsx")
    parser.add_argument("--output", default="", help="输出 profile 路径，例如 profiles/template_auto.json")
    parser.add_argument("--use-llm", action="store_true", help="是否启用本地模型增强字段推断")
    args = parser.parse_args()

    template_path = args.template
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"找不到模板文件：{template_path}")

    if args.output.strip():
        output_path = args.output
    else:
        stem = Path(template_path).stem
        output_path = f"profiles/{stem}_auto.json"

    profile = generate_profile_from_template(
        template_path=template_path,
        use_llm=args.use_llm
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    print("=== 自动生成的 profile ===")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    print(f"\n已保存到：{output_path}")


if __name__ == "__main__":
    main()