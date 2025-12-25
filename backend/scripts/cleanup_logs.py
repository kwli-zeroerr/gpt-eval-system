#!/usr/bin/env python3
"""
清理日志文件脚本
按照 delete_log_files 的逻辑，清理所有具有相同 request_id 的相关文件
"""
import sys
import re
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.paths import DATA_BACKEND_DIR, DATA_FRONTEND_DIR, DATA_EXPORT_DIR, DATA_RETRIEVAL_DIR, DATA_EVALUATION_DIR
from services.format_converter import delete_log_files

def extract_request_id_from_txt(txt_file: Path) -> str:
    """从 TXT 文件中提取 request_id"""
    try:
        with open(txt_file, "r", encoding="utf-8") as f:
            content = f.read()
            match = re.search(r"请求ID:\s*([a-f0-9\-]+)", content, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    except Exception as e:
        print(f"读取 {txt_file} 失败: {e}")
    return ""

def list_all_logs():
    """列出所有日志文件及其 request_id"""
    logs = []
    
    # 从 backend 目录读取 TXT 文件
    if DATA_BACKEND_DIR.exists():
        for txt_file in DATA_BACKEND_DIR.glob("questions_*.txt"):
            request_id = extract_request_id_from_txt(txt_file)
            logs.append({
                "file": txt_file,
                "type": "TXT",
                "request_id": request_id,
                "request_id_prefix": request_id[:8] if len(request_id) >= 8 else request_id
            })
    
    return logs

def find_related_files(request_id: str, request_id_prefix: str):
    """查找所有相关的文件"""
    related = {
        "txt_files": [],
        "json_files": [],
        "csv_files": [],
        "retrieval_files": [],
        "evaluation_files": []
    }
    
    # TXT 文件
    if DATA_BACKEND_DIR.exists():
        for txt_file in DATA_BACKEND_DIR.glob("questions_*.txt"):
            if request_id_prefix in txt_file.stem or request_id in txt_file.stem:
                related["txt_files"].append(txt_file)
            else:
                # 检查内容
                try:
                    with open(txt_file, "r", encoding="utf-8") as f:
                        content = f.read()
                        if request_id_prefix in content or request_id in content:
                            related["txt_files"].append(txt_file)
                except:
                    pass
    
    # JSON 文件
    if DATA_FRONTEND_DIR.exists():
        for json_file in DATA_FRONTEND_DIR.glob("questions_*.json"):
            if request_id_prefix in json_file.stem or request_id in json_file.stem:
                related["json_files"].append(json_file)
    
    # CSV 文件（export）
    if DATA_EXPORT_DIR.exists():
        for csv_file in DATA_EXPORT_DIR.glob("questions_*.csv"):
            if request_id_prefix in csv_file.stem or request_id in csv_file.stem:
                related["csv_files"].append(csv_file)
    
    # 检索结果文件
    if DATA_RETRIEVAL_DIR.exists():
        for csv_file in DATA_RETRIEVAL_DIR.glob("*_with_answers.csv"):
            if request_id_prefix in csv_file.stem or request_id in csv_file.stem:
                related["retrieval_files"].append(csv_file)
    
    # 评测结果文件
    if DATA_EVALUATION_DIR.exists():
        for csv_file in DATA_EVALUATION_DIR.glob("**/*.csv"):
            if request_id_prefix in csv_file.stem or request_id in str(csv_file):
                related["evaluation_files"].append(csv_file)
        for json_file in DATA_EVALUATION_DIR.glob("**/evaluation_summary.json"):
            if request_id_prefix in json_file.stem or request_id in str(json_file):
                related["evaluation_files"].append(json_file)
    
    return related

def main():
    """主函数：列出所有日志并清理"""
    print("=" * 60)
    print("日志文件清理工具")
    print("=" * 60)
    print()
    
    # 列出所有日志
    logs = list_all_logs()
    
    if not logs:
        print("没有找到日志文件")
        return
    
    print(f"找到 {len(logs)} 个日志文件：\n")
    
    # 按 request_id 分组
    grouped = {}
    for log in logs:
        request_id = log["request_id"]
        if not request_id:
            print(f"⚠️  {log['file'].name}: 无法提取 request_id")
            continue
        
        if request_id not in grouped:
            grouped[request_id] = []
        grouped[request_id].append(log)
    
    print(f"共 {len(grouped)} 个不同的 request_id：\n")
    
    # 显示每个 request_id 的相关文件
    for request_id, log_list in grouped.items():
        print(f"Request ID: {request_id[:8]}... ({request_id})")
        print(f"  日志文件: {len(log_list)} 个")
        
        # 查找所有相关文件
        related = find_related_files(request_id, request_id[:8])
        
        total_files = (
            len(related["txt_files"]) +
            len(related["json_files"]) +
            len(related["csv_files"]) +
            len(related["retrieval_files"]) +
            len(related["evaluation_files"])
        )
        
        print(f"  相关文件总数: {total_files}")
        if related["txt_files"]:
            print(f"    - TXT: {len(related['txt_files'])} 个")
        if related["json_files"]:
            print(f"    - JSON: {len(related['json_files'])} 个")
        if related["csv_files"]:
            print(f"    - CSV (export): {len(related['csv_files'])} 个")
        if related["retrieval_files"]:
            print(f"    - 检索结果: {len(related['retrieval_files'])} 个")
        if related["evaluation_files"]:
            print(f"    - 评测结果: {len(related['evaluation_files'])} 个")
        print()
    
    # 询问是否清理
    print("=" * 60)
    print("清理选项：")
    print("1. 清理所有日志文件（删除所有相关文件）")
    print("2. 清理指定的 request_id")
    print("3. 只显示，不删除")
    print()
    
    choice = input("请选择 (1/2/3): ").strip()
    
    if choice == "1":
        # 清理所有
        print("\n开始清理所有日志文件...")
        for request_id, log_list in grouped.items():
            # 使用第一个日志文件作为入口
            log_file = log_list[0]["file"]
            print(f"\n清理 {request_id[:8]}... ({log_file.name})")
            result = delete_log_files(str(log_file))
            print(f"  结果: {result}")
        print("\n✓ 清理完成")
    
    elif choice == "2":
        # 清理指定的
        request_id_input = input("请输入 request_id 前缀（8位）或完整 ID: ").strip()
        found = False
        for request_id, log_list in grouped.items():
            if request_id.startswith(request_id_input) or request_id_input in request_id:
                found = True
                log_file = log_list[0]["file"]
                print(f"\n清理 {request_id}... ({log_file.name})")
                result = delete_log_files(str(log_file))
                print(f"  结果: {result}")
                break
        if not found:
            print(f"未找到匹配的 request_id: {request_id_input}")
    
    else:
        print("\n只显示，未删除任何文件")

if __name__ == "__main__":
    main()

