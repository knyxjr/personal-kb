#!/usr/bin/env python3
"""
从倒排索引自动学习项目关键词映射

用法：
    kb_learn_project_keywords.py --dry-run        # 预览学到的映射
    kb_learn_project_keywords.py --apply          # 写入 config.json
    kb_learn_project_keywords.py --export FILE    # 导出到独立文件
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

# 动态导入
sys.path.insert(0, str(Path(__file__).parent))
from kb_lib import kb_base_dir, load_config

# 导入倒排索引
import importlib.util
_kb_ii_path = Path(__file__).parent.parent / "backend" / "inverted_index.py"
spec = importlib.util.spec_from_file_location("inverted_index", _kb_ii_path)
ii_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ii_module)
InvertedIndex = ii_module.InvertedIndex
get_inverted_index_path = ii_module.get_inverted_index_path


def learn_project_keywords(
    min_search_count: int = 2,
    min_keyword_frequency: int = 2,
    top_n_per_repo: int = 10
) -> dict:
    """从倒排索引学习项目关键词映射

    Args:
        min_search_count: 关键词最小搜索次数
        min_keyword_frequency: 关键词在该 repo 中最小出现次数
        top_n_per_repo: 每个 repo 最多保留 N 个关键词

    Returns:
        {repo_name: {"keywords": [...], "weight": 1.0}}
    """
    base = kb_base_dir()
    index_path = get_inverted_index_path(base)
    index = InvertedIndex(index_path)
    index.load()

    # 统计：repo → {keyword: count}
    repo_keyword_freq = defaultdict(lambda: defaultdict(int))

    # 遍历所有关键词
    for keyword, info in index._index.items():
        search_count = info.get("search_count", 0)

        # 过滤低频关键词
        if search_count < min_search_count:
            continue

        # 统计该关键词在各个 repo 中的出现次数
        buckets = info.get("buckets", [])
        for bucket in buckets:
            parts = bucket.split("/")
            if not parts:
                continue

            repo = parts[0]

            # 跳过特殊桶
            if repo in ("_global", "no-repo", "test-repo"):
                continue

            repo_keyword_freq[repo][keyword] += 1

    # 为每个 repo 选择高频关键词
    project_keywords = {}
    for repo, keyword_counts in repo_keyword_freq.items():
        # 过滤低频关键词
        filtered = {kw: cnt for kw, cnt in keyword_counts.items() if cnt >= min_keyword_frequency}

        if not filtered:
            continue

        # 按频次降序排序，取 top N
        sorted_keywords = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
        top_keywords = [kw for kw, cnt in sorted_keywords[:top_n_per_repo]]

        project_keywords[repo] = {
            "keywords": top_keywords,
            "weight": 1.0
        }

    return project_keywords


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="从倒排索引自动学习项目关键词映射"
    )
    parser.add_argument("--dry-run", action="store_true", help="预览学到的映射，不写入")
    parser.add_argument("--apply", action="store_true", help="写入 config.json")
    parser.add_argument("--export", help="导出到独立 JSON 文件")
    parser.add_argument("--min-search", type=int, default=2, help="关键词最小搜索次数（默认2）")
    parser.add_argument("--min-freq", type=int, default=2, help="关键词在 repo 中最小出现次数（默认2）")
    parser.add_argument("--top-n", type=int, default=10, help="每个 repo 最多保留 N 个关键词（默认10）")

    args = parser.parse_args(argv)

    # 学习映射
    project_keywords = learn_project_keywords(
        min_search_count=args.min_search,
        min_keyword_frequency=args.min_freq,
        top_n_per_repo=args.top_n
    )

    if not project_keywords:
        sys.stderr.write("未学习到任何项目关键词映射（KB 数据不足或阈值过高）\n")
        return 1

    # 输出结果
    output = json.dumps(project_keywords, ensure_ascii=False, indent=2)

    if args.dry_run or (not args.apply and not args.export):
        print("学到的项目关键词映射：\n")
        print(output)
        print(f"\n共 {len(project_keywords)} 个项目")
        return 0

    if args.apply:
        config_path = Path(__file__).parent.parent / "config.json"
        config = load_config()
        config.setdefault("smart_routing", {})["project_keywords"] = project_keywords

        with config_path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print(f"✓ 已写入 {config_path}")
        print(f"  更新了 {len(project_keywords)} 个项目的关键词映射")

    if args.export:
        export_path = Path(args.export)
        with export_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(output + "\n")

        print(f"✓ 已导出到 {export_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
