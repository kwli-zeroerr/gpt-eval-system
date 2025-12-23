# RagFlow 脚本使用指南

## 前置准备

### 1. 配置环境变量

确保 `backend/.env` 文件中包含以下配置：

```bash
RAGFLOW_API_URL=http://192.168.2.168:9222
RAGFLOW_API_KEY=ragflow-你的API密钥
```

### 2. 准备数据文件

确保已运行数据导出脚本，生成：
- `backend/data/ragflow_import/segments.csv`

---

## 脚本列表

### 1. `ragflow_import_from_segments.py` - 导入数据到 RagFlow

**功能**：将 MinIO OCR 导出的数据导入到 RagFlow

**工作流程**：
1. **删除**：同步 `datasets.json`，清理当前 API key 无权限的旧数据集
2. **上传**：导入新数据到 RagFlow，并更新 `datasets.json`

**使用方法**：

```bash
cd /home/zeroerr-ai71/kiven/gpt-eval-system/backend
source venv/bin/activate
python -m scripts.ragflow_import_from_segments
```

**执行步骤**：

```
[步骤 1/2] 删除：同步 datasets.json，清理无权限的旧数据集
  - 从 RagFlow API 获取当前 API key 有权限的所有数据集
  - 更新 datasets.json，只保留有权限的数据

[步骤 2/2] 上传：导入新数据到 RagFlow
  - 读取 segments.csv
  - 删除同名旧数据集和文档（避免名称冲突）
  - 上传文件并转换为文档
  - 添加 chunks
  - 更新 datasets.json
```

**输出**：
- 在 RagFlow 中创建/更新数据集和文档
- 更新 `backend/datasets.json`（只包含当前 API key 有权限的数据集）

---

### 2. `ragflow_cleanup.py` - 清理 RagFlow 数据集

**功能**：列出和删除 RagFlow 中的数据集

**使用方法**：

```bash
cd /home/zeroerr-ai71/kiven/gpt-eval-system/backend
source venv/bin/activate

# 列出所有数据集
python -m scripts.ragflow_cleanup --list

# 精确删除一个数据集（按名称）
python -m scripts.ragflow_cleanup --delete-dataset "eCoder 用户手册"

# 按前缀批量删除（例如所有以 "eCoder" 开头的知识库）
python -m scripts.ragflow_cleanup --delete-prefix "eCoder"

# 删除所有数据集（需要确认）
python -m scripts.ragflow_cleanup --delete-all
```

**参数说明**：
- `--list`：列出所有数据集（默认行为）
- `--delete-dataset <name>`：精确删除指定名称的数据集
- `--delete-prefix <prefix>`：按前缀批量删除数据集
- `--delete-all`：删除所有数据集（会要求确认）

---

### 3. `test_s6_question.py` - 测试 S6 问题回答质量

**功能**：测试 RagFlow API 对 S6（对抗数据/敏感信息）类型问题的回答质量

**使用方法**：

```bash
cd /home/zeroerr-ai71/kiven/gpt-eval-system/backend
source venv/bin/activate
python -m scripts.test_s6_question
```

**功能说明**：
- 使用预定义的 S6 测试问题集
- 检查回答是否包含 "not found" 等不当响应
- 验证回答的人性化和专业性
- 自动清理测试 session

---

## 典型工作流程

### 场景 1：首次导入数据

```bash
# 1. 确保 RagFlow 正在运行
# 2. 确保 segments.csv 已准备好

# 3. 导入数据（会自动清理旧数据并上传新数据）
python -m scripts.ragflow_import_from_segments

# 4. 验证导入结果（可选：使用 test_s6_question.py 测试）
python -m scripts.test_s6_question
```

### 场景 2：重新导入数据（清理后重新导入）

```bash
# 1. 清理所有旧数据集（可选）
python -m scripts.ragflow_cleanup --delete-all

# 2. 重新导入数据
python -m scripts.ragflow_import_from_segments
```

### 场景 3：只清理特定数据集

```bash
# 1. 列出所有数据集，找到要删除的 ID
python -m scripts.ragflow_cleanup

# 2. 删除指定数据集
python -m scripts.ragflow_cleanup --delete <dataset_id>
```

---

## 重要说明

### datasets.json 的维护

- **自动同步**：`ragflow_import_from_segments.py` 会自动同步 `datasets.json`
- **只包含有权限的数据**：`datasets.json` 只包含当前 `.env` 中 `RAGFLOW_API_KEY` 有权限的数据集
- **格式**：
  ```json
  {
    "数据集名称": {
      "id": "dataset-id",
      "documents": {
        "文档名称": "document-id"
      }
    }
  }
  ```

### API Key 权限

- 每个 API key 只能访问属于其用户（tenant）的数据集
- 如果更换 API key，需要重新运行导入脚本以同步 `datasets.json`
- 脚本会自动验证权限，只保留有权限的数据集

### 数据命名冲突

- 脚本会自动处理命名冲突：
  - 删除同名数据集（包括所有变体：`name`, `name(1)`, `name(2)` 等）
  - 删除同名文档（包括所有变体）
  - 从数据库直接删除，确保彻底清理

---

## 故障排查

### 问题 1：导入时出现 "You don't own the dataset" 错误

**原因**：`datasets.json` 中包含旧的数据集 ID，当前 API key 无权限访问

**解决**：
- 脚本会自动处理：步骤 1 会清理无权限的数据集
- 如果仍有问题，手动运行清理脚本：
  ```bash
  python -m scripts.ragflow_cleanup --delete-all
  python -m scripts.ragflow_import_from_segments
  ```

### 问题 2：导入时出现名称冲突（如 `name(2)`, `name(3)`）

**原因**：RagFlow 检测到同名数据集/文档

**解决**：
- 脚本已自动处理：会删除所有同名变体
- 如果仍有问题，检查数据库是否有软删除的记录：
  ```bash
  # 检查数据库
  python -c "
  import psycopg2
  conn = psycopg2.connect(host='localhost', port=5432, database='zeroerr_meta', user='zeroerr', password='zero0000', options='-c search_path=ragflow')
  cur = conn.cursor()
  cur.execute(\"SELECT name FROM knowledgebase WHERE name LIKE '%(%)%' AND status = '1'\")
  print(cur.fetchall())
  conn.close()
  "
  ```

### 问题 3：检索失败，返回 code=102

**原因**：使用了无权限的数据集 ID

**解决**：
- 确保 `datasets.json` 已同步：重新运行导入脚本
- 检查 API key 是否正确：确认 `.env` 中的 `RAGFLOW_API_KEY` 有效

---

## 快速参考

```bash
# 激活虚拟环境
cd /home/zeroerr-ai71/kiven/gpt-eval-system/backend
source venv/bin/activate

# 导入数据（推荐：自动清理+上传）
python -m scripts.ragflow_import_from_segments

# 清理所有数据集
python -m scripts.ragflow_cleanup --delete-all

# 测试 S6 问题回答（可选）
python -m scripts.test_s6_question
```

