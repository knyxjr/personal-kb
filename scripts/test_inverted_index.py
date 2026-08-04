#!/usr/bin/env python3
"""
测试倒排索引功能
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# 添加脚本目录到路径
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from kb_lib import kb_base_dir, read_jsonl

# 导入倒排索引模块
try:
    import importlib.util

    backend_path = script_dir.parent / "backend" / "inverted_index.py"
    spec = importlib.util.spec_from_file_location("inverted_index", backend_path)
    if spec and spec.loader:
        inverted_index_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inverted_index_module)
        InvertedIndex = inverted_index_module.InvertedIndex
        get_inverted_index_path = inverted_index_module.get_inverted_index_path
    else:
        print("错误：无法加载倒排索引模块")
        sys.exit(1)
except Exception as e:
    print(f"错误：无法导入倒排索引模块: {e}")
    sys.exit(1)


def test_basic_operations():
    """测试基本操作"""
    print("=== 测试 1: 基本操作 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = InvertedIndex(index_path)

        # 添加条目
        index.add_entry("entry1", ["工单", "项目甲", "dashboard"], "project-a/main")
        index.add_entry("entry2", ["工单", "项目乙"], "project-b/main")
        index.add_entry("entry3", ["token", "超时"], "project-a/main")

        # 保存
        assert index.save(), "保存失败"

        # 重新加载
        index2 = InvertedIndex(index_path)
        assert index2.load(), "加载失败"

        # 查询
        entries = index2.get_entries_by_keyword("工单")
        assert len(entries) == 2, f"期望 2 个条目，实际 {len(entries)}"
        assert "entry1" in entries, "entry1 应该在结果中"
        assert "entry2" in entries, "entry2 应该在结果中"

        print("✓ 基本操作测试通过")


def test_search_count():
    """测试搜索计数"""
    print("\n=== 测试 2: 搜索计数 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = InvertedIndex(index_path)

        # 初始搜索计数为 0
        info = index.get_keyword_info("工单")
        assert info is None, "关键词不应存在"

        # 第一次搜索
        index.update_search_count(["工单"])
        info = index.get_keyword_info("工单")
        assert info is not None, "关键词应该存在"
        assert info["search_count"] == 1, f"期望搜索计数为 1，实际 {info['search_count']}"

        # 第二次搜索
        index.update_search_count(["工单"])
        info = index.get_keyword_info("工单")
        assert info["search_count"] == 2, f"期望搜索计数为 2，实际 {info['search_count']}"

        # 保存并重新加载
        index.save()
        index2 = InvertedIndex(index_path)
        index2.load()
        info2 = index2.get_keyword_info("工单")
        assert info2["search_count"] == 2, "搜索计数应该持久化"

        print("✓ 搜索计数测试通过")


def test_aggregation_candidates():
    """测试聚合候选检测"""
    print("\n=== 测试 3: 聚合候选检测 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = InvertedIndex(index_path)

        # 添加多个条目（满足聚合条件）
        index.add_entry("e1", ["工单"], "project-a/main")
        index.add_entry("e2", ["工单"], "project-b/main")
        index.add_entry("e3", ["工单"], "project-c/main")

        # 搜索 5 次
        for _ in range(5):
            index.update_search_count(["工单"])

        # 检查聚合候选
        candidates = index.get_aggregation_candidates(
            min_search_count=5,
            min_entries=3,
            min_buckets=2
        )

        assert len(candidates) == 1, f"期望 1 个候选，实际 {len(candidates)}"
        assert candidates[0]["keyword"] == "工单", "候选关键词应该是工单"

        # 添加不满足条件的关键词
        index.add_entry("e4", ["token"], "project-a/main")
        index.update_search_count(["token"])

        candidates2 = index.get_aggregation_candidates(
            min_search_count=5,
            min_entries=3,
            min_buckets=2
        )

        assert len(candidates2) == 1, "token 不应该出现在候选中"

        print("✓ 聚合候选检测测试通过")


def test_rebuild_from_entries():
    """测试从条目重建索引"""
    print("\n=== 测试 4: 从条目重建索引 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = InvertedIndex(index_path)

        # 模拟条目数据
        entries = [
            {
                "id": "e1",
                "repo": "test-repo",
                "branch": "main",
                "tags": ["工单", "项目甲"],
                "aliases": ["supervision"],
                "trigger_terms": ["task-system"]
            },
            {
                "id": "e2",
                "repo": "test-repo",
                "branch": "dev",
                "tags": ["工单", "项目乙"],
                "aliases": [],
                "trigger_terms": []
            }
        ]

        # 重建索引
        keyword_count = index.rebuild_from_entries(entries)
        assert keyword_count > 0, "应该有关键词"

        # 验证索引内容
        info = index.get_keyword_info("工单")
        assert info is not None, "工单应该在索引中"
        assert len(info["entry_ids"]) == 2, "工单应该关联 2 个条目"
        assert len(info["buckets"]) == 2, "工单应该跨 2 个桶"

        print("✓ 从条目重建索引测试通过")


def test_stats():
    """测试统计功能"""
    print("\n=== 测试 5: 统计功能 ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.json"
        index = InvertedIndex(index_path)

        # 添加条目和搜索
        index.add_entry("e1", ["工单", "项目甲"], "project-a/main")
        index.add_entry("e2", ["工单"], "project-b/main")
        index.update_search_count(["工单"] * 5)
        index.update_search_count(["项目甲"] * 2)

        # 获取统计
        stats = index.get_stats()

        assert stats["total_keywords"] == 2, "应该有 2 个关键词"
        assert stats["total_entries"] == 2, "应该有 2 个条目"
        assert stats["total_searches"] == 7, "总搜索次数应该是 7"
        assert len(stats["hot_keywords"]) == 2, "热门关键词列表应该有 2 项"
        assert stats["hot_keywords"][0]["keyword"] == "工单", "工单应该是最热关键词"

        print("✓ 统计功能测试通过")


def test_real_kb():
    """测试真实 KB 数据"""
    print("\n=== 测试 6: 真实 KB 数据 ===")

    base = kb_base_dir()
    if not base.exists():
        print("⊗ 跳过（KB 目录不存在）")
        return

    index_path = get_inverted_index_path(base)

    # 收集真实条目
    entries = []
    for repo_dir in base.iterdir():
        if not repo_dir.is_dir():
            continue
        for branch_dir in repo_dir.iterdir():
            if not branch_dir.is_dir():
                continue
            kb_path = branch_dir / "kb.jsonl"
            if not kb_path.exists():
                continue
            for entry in read_jsonl(kb_path):
                if not entry.get("_deleted"):
                    entries.append(entry)

    if not entries:
        print("⊗ 跳过（没有条目）")
        return

    print(f"  扫描到 {len(entries)} 个条目")

    # 重建索引
    index = InvertedIndex(index_path)
    keyword_count = index.rebuild_from_entries(entries)

    print(f"  生成 {keyword_count} 个关键词")

    # 保存索引
    if index.save():
        print(f"  索引已保存到: {index_path}")

    # 显示统计
    stats = index.get_stats()
    print(f"  总条目: {stats['total_entries']}")
    print(f"  总关键词: {stats['total_keywords']}")

    if stats["hot_keywords"]:
        print("  热门关键词:")
        for item in stats["hot_keywords"][:5]:
            print(f"    {item['keyword']}: {item['search_count']} 次")

    print("✓ 真实 KB 数据测试通过")


def main():
    print("倒排索引功能测试\n")

    try:
        test_basic_operations()
        test_search_count()
        test_aggregation_candidates()
        test_rebuild_from_entries()
        test_stats()
        test_real_kb()

        print("\n" + "=" * 50)
        print("✓ 所有测试通过")
        return 0
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ 运行错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
