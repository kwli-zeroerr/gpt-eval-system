# RagFlow 后端设置详细说明

本文档详细说明如何配置 RagFlow API 以使用检索和评测模块。

## 1. 获取 RagFlow API 信息

### 1.1 API URL 和 API Key

1. **API URL**：RagFlow 服务的 API 地址
   - 格式：`http://your-ragflow-host:port` 或 `https://your-ragflow-host:port`
   - 示例：`http://localhost:9380` 或 `https://ragflow.example.com`

2. **API Key**：RagFlow API 的认证密钥
   - 在 RagFlow 管理界面中生成或获取
   - 通常是一个 Bearer Token 格式的字符串

### 1.2 数据集和文档 ID 映射

RagFlow 使用数据集（Dataset）和文档（Document）的概念。为了正确检索，需要创建一个 `datasets.json` 文件来映射数据集和文档 ID。

#### 创建 `datasets.json` 文件

在 `backend/` 目录下创建 `datasets.json` 文件，格式如下：

```json
{
  "datasets": [
    {
      "dataset_id": "your-dataset-id-1",
      "dataset_name": "eRob用户手册",
      "document_ids": [
        "doc-id-1",
        "doc-id-2"
      ]
    },
    {
      "dataset_id": "your-dataset-id-2",
      "dataset_name": "eCoder用户手册",
      "document_ids": [
        "doc-id-3",
        "doc-id-4"
      ]
    }
  ]
}
```

#### 如何获取 Dataset ID 和 Document ID

**方法一：通过 RagFlow API**

```bash
# 1. 列出所有数据集
curl -X GET "http://your-ragflow-host:port/api/v1/datasets" \
  -H "Authorization: Bearer YOUR_API_KEY"

# 2. 列出某个数据集下的文档
curl -X GET "http://your-ragflow-host:port/api/v1/datasets/{dataset_id}/documents" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

**方法二：通过 RagFlow Web 界面**

1. 登录 RagFlow Web 管理界面
2. 在"数据集"页面查看数据集 ID
3. 点击进入数据集，查看文档列表和文档 ID

## 2. 配置环境变量

编辑 `backend/.env` 文件，添加以下配置：

```bash
#############
# RagFlow API Configuration
#############
# RagFlow API 地址（必需）
RAGFLOW_API_URL=http://your-ragflow-host:port

# RagFlow API Key（必需）
RAGFLOW_API_KEY=your-ragflow-api-key

# datasets.json 文件路径（可选，默认为 datasets.json）
RAGFLOW_DATASETS_JSON=datasets.json

# 检索参数配置（可选）
RAGFLOW_TOP_K=5                    # 检索返回的 top-k 结果数
RAGFLOW_SIMILARITY_THRESHOLD=0.0   # 相似度阈值（0.0-1.0）
RAGFLOW_VECTOR_SIMILARITY_WEIGHT=0.3  # 向量相似度权重（0.0-1.0）

# 并发和延迟配置（可选）
RAGFLOW_MAX_WORKERS=1              # 并发请求数（建议 1-5）
RAGFLOW_DELAY=0.5                  # 请求间隔（秒，避免 API 限流）
```

### 配置说明

- **RAGFLOW_API_URL**：RagFlow API 的基础 URL，不包含路径（如 `/api/v1`）
- **RAGFLOW_API_KEY**：API 认证密钥，格式通常是 Bearer Token
- **RAGFLOW_DATASETS_JSON**：数据集映射文件路径，相对于 `backend/` 目录
- **RAGFLOW_TOP_K**：检索时返回的最相关结果数量，建议 3-10
- **RAGFLOW_SIMILARITY_THRESHOLD**：相似度阈值，低于此值的结果会被过滤
- **RAGFLOW_VECTOR_SIMILARITY_WEIGHT**：向量相似度在综合评分中的权重
- **RAGFLOW_MAX_WORKERS**：并发请求数，根据 API 限流情况调整
- **RAGFLOW_DELAY**：请求间隔，避免触发 API 限流

## 3. 测试配置

### 3.1 测试 API 连接

可以使用以下 Python 脚本测试 RagFlow API 连接：

```python
import requests
import os
from dotenv import load_dotenv

load_dotenv()

api_url = os.getenv("RAGFLOW_API_URL")
api_key = os.getenv("RAGFLOW_API_KEY")

# 测试列出数据集
response = requests.get(
    f"{api_url}/api/v1/datasets",
    headers={"Authorization": f"Bearer {api_key}"}
)

print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
```

### 3.2 测试检索功能

```python
from services.ragflow_client import RagFlowClient

client = RagFlowClient(api_url, api_key)

# 测试搜索
result = client.search(
    query="测试问题",
    dataset_ids=["your-dataset-id"],
    top_k=5
)

print(result)
```

## 4. 常见问题

### 4.1 API 连接失败

**问题**：`ConnectionError` 或 `401 Unauthorized`

**解决方案**：
- 检查 `RAGFLOW_API_URL` 是否正确
- 检查 `RAGFLOW_API_KEY` 是否有效
- 确认 RagFlow 服务是否正在运行
- 检查防火墙和网络配置

### 4.2 数据集或文档未找到

**问题**：`404 Not Found` 或检索结果为空

**解决方案**：
- 检查 `datasets.json` 中的 ID 是否正确
- 确认数据集和文档在 RagFlow 中确实存在
- 使用 API 或 Web 界面验证 ID

### 4.3 API 限流

**问题**：`429 Too Many Requests`

**解决方案**：
- 减小 `RAGFLOW_MAX_WORKERS`（建议设为 1）
- 增大 `RAGFLOW_DELAY`（建议 0.5-1.0 秒）
- 联系 RagFlow 管理员提高限流阈值

### 4.4 检索结果不准确

**问题**：检索到的答案与问题不匹配

**解决方案**：
- 调整 `RAGFLOW_TOP_K`，增加检索结果数量
- 调整 `RAGFLOW_SIMILARITY_THRESHOLD`，过滤低质量结果
- 调整 `RAGFLOW_VECTOR_SIMILARITY_WEIGHT`，平衡向量和关键词匹配

## 5. 完整配置示例

```bash
# backend/.env
RAGFLOW_API_URL=http://localhost:9380
RAGFLOW_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
RAGFLOW_DATASETS_JSON=datasets.json
RAGFLOW_TOP_K=5
RAGFLOW_SIMILARITY_THRESHOLD=0.0
RAGFLOW_VECTOR_SIMILARITY_WEIGHT=0.3
RAGFLOW_MAX_WORKERS=1
RAGFLOW_DELAY=0.5
```

```json
// backend/datasets.json
{
  "datasets": [
    {
      "dataset_id": "ds-001",
      "dataset_name": "eRob用户手册",
      "document_ids": ["doc-001", "doc-002"]
    },
    {
      "dataset_id": "ds-002",
      "dataset_name": "eCoder用户手册",
      "document_ids": ["doc-003", "doc-004"]
    }
  ]
}
```

## 6. 验证配置

配置完成后，运行以下命令验证：

```bash
cd backend
source venv/bin/activate
python -c "
from services.ragflow_client import RagFlowClient
import os
from dotenv import load_dotenv

load_dotenv()
client = RagFlowClient(
    os.getenv('RAGFLOW_API_URL'),
    os.getenv('RAGFLOW_API_KEY')
)
datasets = client.get_all_datasets_and_documents(os.getenv('RAGFLOW_DATASETS_JSON'))
print('Datasets:', datasets)
"
```

如果输出正常，说明配置成功！

