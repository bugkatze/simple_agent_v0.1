# seed_clean_datafeedback.py - 数据反馈版本：清洗后写入 file_database/current/
import json
import shutil
from pathlib import Path

def clean_comments(input_path: str, output_path: str) -> dict:
    """清洗B站评论数据：删除点赞数小于10的评论，返回统计信息。"""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    original = data['top_comments']
    cleaned = [c for c in original if c.get('like', 0) >= 10]
    removed = [c for c in original if c.get('like', 0) < 10]

    data['top_comments'] = cleaned
    data['total_collected'] = len(cleaned)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 写入 file_database/current/ 触发数据反馈检测
    db_dir = Path("file_database/current")
    db_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "original_count": len(original),
        "cleaned_count": len(cleaned),
        "removed_count": len(removed),
        "removed_likes_distribution": [c['like'] for c in removed]
    }
    (db_dir / "clean_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (db_dir / "removed_comments.json").write_text(
        json.dumps(removed, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    # 同时把清洗结果也复制过去，方便后续分析
    shutil.copy(output_path, db_dir / "cleaned_comments.json")

    print(f"保留 {len(cleaned)} 条，删除 {len(removed)} 条")
    return stats

if __name__ == "__main__":
    stats = clean_comments("comments_BV1GXJs6kEt3.json", "cleaned_comments.json")
# END_AND_END
