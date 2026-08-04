"""
搜索结果增强器：在搜索结果中注入聚合视图

这是一个辅助模块，为 kb_search.py 提供聚合视图支持。
不修改原有 kb_search.py 逻辑，而是在搜索结果前插入聚合视图。

使用方式：
1. kb_search.py 正常执行搜索
2. 在返回结果前，调用此模块检查是否有聚合视图
3. 如果有聚合视图，在结果列表开头插入聚合视图条目
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

# 动态导入 bucket_aggregator
try:
    import importlib.util

    _kb_backend_path = Path(__file__).parent.parent / "backend" / "bucket_aggregator.py"
    if _kb_backend_path.exists():
        spec = importlib.util.spec_from_file_location("bucket_aggregator", _kb_backend_path)
        if spec and spec.loader:
            _bucket_aggregator_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_bucket_aggregator_module)
            BucketAggregator = _bucket_aggregator_module.BucketAggregator
            get_bucket_aggregator_path = _bucket_aggregator_module.get_bucket_aggregator_path
            _BUCKET_AGGREGATOR_AVAILABLE = True
        else:
            _BUCKET_AGGREGATOR_AVAILABLE = False
    else:
        _BUCKET_AGGREGATOR_AVAILABLE = False
except Exception:
    _BUCKET_AGGREGATOR_AVAILABLE = False
    BucketAggregator = None
    get_bucket_aggregator_path = None


def inject_aggregation_view(
    query: str,
    search_results: List[Dict[str, Any]],
    kb_base_dir: Path
) -> List[Dict[str, Any]]:
    """
    在搜索结果中注入聚合视图（如果存在）

    Args:
        query: 查询关键词
        search_results: 原始搜索结果
        kb_base_dir: KB 基础目录

    Returns:
        注入聚合视图后的结果列表
    """
    if not _BUCKET_AGGREGATOR_AVAILABLE or not BucketAggregator or not get_bucket_aggregator_path:
        return search_results

    if not query or not query.strip():
        return search_results

    # 初始化聚合器
    agg_dir = get_bucket_aggregator_path(kb_base_dir)
    aggregator = BucketAggregator(agg_dir)

    # 检查是否有聚合视图
    aggregation = aggregator.load_aggregation(query)
    if not aggregation:
        return search_results

    # 检查新鲜度
    freshness = aggregator.check_freshness(query, search_results)

    # 构建聚合视图展示条目
    agg_entry = {
        "id": aggregation.get("id", ""),
        "type": "aggregation",
        "title": f"📊 {aggregation.get('title', '')}",
        "story": _build_aggregation_story(aggregation, freshness),
        "ts": aggregation.get("created_ts", ""),
        "repo": "_aggregation",
        "branch": query,
        "tags": ["聚合视图"] + aggregation.get("common_tags", [])[:5],
        "source": "aggregation",
        "_is_aggregation": True,
        "_aggregation_data": aggregation,
        "_freshness": freshness
    }

    # 在结果列表开头插入聚合视图
    return [agg_entry] + search_results


def _build_aggregation_story(
    aggregation: Dict[str, Any],
    freshness: Dict[str, Any]
) -> str:
    """
    构建聚合视图的 story 字段（用于显示）

    Args:
        aggregation: 聚合视图数据
        freshness: 新鲜度检查结果

    Returns:
        格式化的 story 文本
    """
    lines = []

    # 基本信息
    story = aggregation.get("story", "")
    if story:
        lines.append(story)

    # 项目列表（最多显示 10 个）
    projects = aggregation.get("projects", [])
    if projects:
        lines.append(f"\n涵盖项目（{len(projects)} 个）：")
        for proj in projects[:10]:
            name = proj.get("name", "")
            bucket = proj.get("bucket", "")
            lines.append(f"  • {name} ({bucket})")
        if len(projects) > 10:
            lines.append(f"  ... 还有 {len(projects) - 10} 个项目")

    # 共同标签
    common_tags = aggregation.get("common_tags", [])
    if common_tags:
        lines.append(f"\n共同标签：{', '.join(common_tags[:10])}")

    # 新鲜度信息
    if not freshness.get("is_fresh"):
        new_entries = freshness.get("new_entries", [])
        missing_entries = freshness.get("missing_entries", [])
        if new_entries or missing_entries:
            lines.append("\n⚠️  聚合视图已过期：")
            if new_entries:
                lines.append(f"  • 新增 {len(new_entries)} 个条目（见下方搜索结果）")
            if missing_entries:
                lines.append(f"  • 缺失 {len(missing_entries)} 个条目")
            lines.append("  提示：运行 kb_refresh_aggregation.py 刷新聚合视图")

    return "\n".join(lines)


def format_aggregation_entry(entry: Dict[str, Any]) -> str:
    """
    格式化聚合视图条目（用于命令行输出）

    Args:
        entry: 聚合视图条目

    Returns:
        格式化后的文本
    """
    if not entry.get("_is_aggregation"):
        return ""

    lines = []

    # 标题栏
    title = entry.get("title", "")
    lines.append("=" * 80)
    lines.append(f"{title}")
    lines.append("=" * 80)

    # 内容
    story = entry.get("story", "")
    if story:
        lines.append(story)

    lines.append("=" * 80)
    lines.append("")

    return "\n".join(lines)


def check_aggregation_trigger(
    query: str,
    kb_base_dir: Path
) -> Optional[str]:
    """
    检查查询是否触发聚合条件

    Args:
        query: 查询关键词
        kb_base_dir: KB 基础目录

    Returns:
        如果需要聚合，返回提示信息；否则返回 None
    """
    if not _BUCKET_AGGREGATOR_AVAILABLE or not BucketAggregator or not get_bucket_aggregator_path:
        return None

    # 动态导入 inverted_index（检查触发条件）
    try:
        import importlib.util

        _kb_backend_path = Path(__file__).parent.parent / "backend" / "inverted_index.py"
        if _kb_backend_path.exists():
            spec = importlib.util.spec_from_file_location("inverted_index", _kb_backend_path)
            if spec and spec.loader:
                _inverted_index_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(_inverted_index_module)
                InvertedIndex = _inverted_index_module.InvertedIndex
                get_inverted_index_path = _inverted_index_module.get_inverted_index_path
            else:
                return None
        else:
            return None
    except Exception:
        return None

    # 加载倒排索引
    index_path = get_inverted_index_path(kb_base_dir)
    index = InvertedIndex(index_path)
    index.load()

    # 检查是否已有聚合视图
    agg_dir = get_bucket_aggregator_path(kb_base_dir)
    aggregator = BucketAggregator(agg_dir)
    if aggregator.exists(query):
        return None  # 已存在聚合视图，不需要触发

    # 获取关键词信息
    keyword_info = index.get_keyword_info(query)
    if not keyword_info:
        return None

    search_count = keyword_info.get("search_count", 0)
    entry_count = len(keyword_info.get("entry_ids", []))
    bucket_count = len(keyword_info.get("buckets", []))

    # 检查触发条件
    MIN_SEARCH_COUNT = 5
    MIN_ENTRIES = 3
    MIN_BUCKETS = 2

    if (search_count >= MIN_SEARCH_COUNT and
        entry_count >= MIN_ENTRIES and
        bucket_count >= MIN_BUCKETS):
        return (
            f"\n💡 提示：关键词 '{query}' 满足聚合条件\n"
            f"   - 搜索次数：{search_count}\n"
            f"   - 条目数：{entry_count}\n"
            f"   - 桶数：{bucket_count}\n"
            f"   建议运行：kb_aggregate.py {query}\n"
        )

    return None
