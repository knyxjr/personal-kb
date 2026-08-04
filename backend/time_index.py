"""
时间索引模块：基于文件系统 mtime 的轻量级时间检索实现

设计原则：
- 数据源：直接读取文件系统 mtime（os.path.getmtime），无需额外存储层
- 索引结构：内存 sorted list，启动时构建，增量更新
- 查询能力：支持相对时间（recent=7d）、绝对时间范围、top-k 限制
- 性能目标：冷启动 < 100ms（1000 条目），查询响应 < 10ms

实现约束：
- 文件系统为唯一真实来源，索引可随时重建
- 不依赖外部数据库或持久化索引文件
- 支持跨平台（Windows/Linux/macOS）
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import json


class TimeIndex:
    """时间索引：条目路径 -> mtime 的有序映射"""

    def __init__(self, kb_root: str):
        """
        Args:
            kb_root: KB 根目录路径
        """
        self.kb_root = Path(kb_root)
        self.entries_dir = self.kb_root / "entries"
        # 索引结构：List[(mtime_timestamp, entry_id, file_path)]
        # 按 mtime 降序排列（最新的在前）
        self._index: List[Tuple[float, str, Path]] = []
        self._last_build_time: Optional[float] = None

    def build(self) -> int:
        """
        全量构建时间索引

        Returns:
            索引的条目数量
        """
        if not self.entries_dir.exists():
            self._index = []
            self._last_build_time = datetime.now().timestamp()
            return 0

        entries = []
        for entry_file in self.entries_dir.glob("*.md"):
            try:
                mtime = entry_file.stat().st_mtime
                entry_id = entry_file.stem
                entries.append((mtime, entry_id, entry_file))
            except (OSError, IOError):
                # 跳过无法访问的文件
                continue

        # 按 mtime 降序排序
        entries.sort(key=lambda x: x[0], reverse=True)
        self._index = entries
        self._last_build_time = datetime.now().timestamp()
        return len(self._index)

    def update_entry(self, entry_id: str) -> bool:
        """
        增量更新单个条目的时间信息

        Args:
            entry_id: 条目 ID

        Returns:
            是否更新成功
        """
        entry_file = self.entries_dir / f"{entry_id}.md"
        if not entry_file.exists():
            # 文件不存在，从索引中移除
            self._index = [(m, eid, p) for m, eid, p in self._index if eid != entry_id]
            return False

        try:
            mtime = entry_file.stat().st_mtime
            # 移除旧记录
            self._index = [(m, eid, p) for m, eid, p in self._index if eid != entry_id]
            # 插入新记录并重新排序
            self._index.append((mtime, entry_id, entry_file))
            self._index.sort(key=lambda x: x[0], reverse=True)
            return True
        except (OSError, IOError):
            return False

    def get_recent_entries(
        self,
        days: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        查询最近更新的条目

        Args:
            days: 最近 N 天（优先级最高，与 since/until 互斥）
            since: 起始时间（包含）
            until: 结束时间（包含）
            limit: 返回条目数量上限

        Returns:
            条目列表，每项包含：
            - entry_id: 条目 ID
            - mtime: 修改时间（ISO 8601 格式）
            - mtime_timestamp: 修改时间戳
            - file_path: 文件路径
        """
        if not self._index:
            return []

        # 确定时间范围
        if days is not None:
            since = datetime.now() - timedelta(days=days)
            until = None

        # 过滤时间范围
        results = []
        for mtime_ts, entry_id, file_path in self._index:
            mtime_dt = datetime.fromtimestamp(mtime_ts)

            # 检查时间范围
            if since and mtime_dt < since:
                continue
            if until and mtime_dt > until:
                continue

            results.append({
                "entry_id": entry_id,
                "mtime": mtime_dt.isoformat(),
                "mtime_timestamp": mtime_ts,
                "file_path": str(file_path)
            })

            # 限制返回数量
            if limit and len(results) >= limit:
                break

        return results

    def get_index_stats(self) -> Dict:
        """
        获取索引统计信息

        Returns:
            统计信息字典
        """
        if not self._index:
            return {
                "total_entries": 0,
                "last_build_time": None,
                "oldest_entry": None,
                "newest_entry": None
            }

        oldest_mtime = datetime.fromtimestamp(self._index[-1][0])
        newest_mtime = datetime.fromtimestamp(self._index[0][0])

        return {
            "total_entries": len(self._index),
            "last_build_time": datetime.fromtimestamp(self._last_build_time).isoformat() if self._last_build_time else None,
            "oldest_entry": {
                "entry_id": self._index[-1][1],
                "mtime": oldest_mtime.isoformat()
            },
            "newest_entry": {
                "entry_id": self._index[0][1],
                "mtime": newest_mtime.isoformat()
            }
        }


def parse_recent_param(recent: str) -> int:
    """
    解析 --recent 参数为天数

    支持格式：
    - 纯数字：7 -> 7 天
    - 带单位：7d, 2w, 1m

    Args:
        recent: 时间参数字符串

    Returns:
        天数

    Raises:
        ValueError: 格式不合法
    """
    recent = recent.strip().lower()

    # 纯数字
    if recent.isdigit():
        return int(recent)

    # 带单位
    if len(recent) < 2:
        raise ValueError(f"Invalid recent format: {recent}")

    value_str = recent[:-1]
    unit = recent[-1]

    if not value_str.isdigit():
        raise ValueError(f"Invalid recent format: {recent}")

    value = int(value_str)

    if unit == 'd':
        return value
    elif unit == 'w':
        return value * 7
    elif unit == 'm':
        return value * 30
    else:
        raise ValueError(f"Unknown unit: {unit}. Supported: d (days), w (weeks), m (months)")


# 单例实例（延迟初始化）
_global_index: Optional[TimeIndex] = None


def get_time_index(kb_root: Optional[str] = None, force_rebuild: bool = False) -> TimeIndex:
    """
    获取全局时间索引实例

    Args:
        kb_root: KB 根目录，首次调用时必须提供
        force_rebuild: 是否强制重建索引

    Returns:
        TimeIndex 实例
    """
    global _global_index

    if _global_index is None:
        if kb_root is None:
            raise ValueError("kb_root must be provided for first-time initialization")
        _global_index = TimeIndex(kb_root)
        _global_index.build()
    elif force_rebuild:
        _global_index.build()

    return _global_index
