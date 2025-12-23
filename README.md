# GPT 售后评测系统 - 运行指南

## 系统概述

本系统是一个**独立的 GPT 售后能力评测平台**，旨在衡量 GPT 售后部分面向用户的泛化能力，即评估 GPT 售后系统在面对不同用户、不同类型问题时的应答能力。

### 核心目标

- **泛化能力评测**：通过生成多样化的问题类型（S1-S6），全面评估 GPT 售后系统的应答能力
- **独立运行**：系统完全隔离于原 GPT 生产环境，不影响原有系统的正常使用
- **标准化流程**：基于相同的文档内容生成问题，采用相同的检索功能获取答案，确保评测的公平性和一致性
- **多维度评估**：结合最先进的 Ragas AI 评测库（从 LLM 角度）和传统章节匹配指标，提供全面的质量评估

### 系统特点

- ✅ **完全隔离**：独立部署，不影响生产环境
- ✅ **标准化评测**：使用相同的文档和检索逻辑，确保评测一致性
- ✅ **先进评测技术**：集成 Ragas AI 评测库，从多个维度评估答案质量
- ✅ **自动化流程**：支持一键运行完整评测流程（问题生成 → 格式转换 → 检索 → 评测）
- ⚠️ **性能说明**：当前版本为单线程模式，速度较慢。在保障功能稳定后，将升级为多并发模式以提升性能

## 系统架构

系统包含4个核心模块，形成完整的自动化评测流程：

1. **问题生成模块** - 从6个维度（S1-S6）生成多样化的测试问题
2. **格式转换模块** - 将问题日志转换为标准 CSV 格式
3. **检索模块** - 批量调用 RagFlow API，使用与生产环境相同的检索逻辑获取答案
4. **评测模块** - 使用章节匹配（传统指标）和 Ragas AI（LLM 角度）进行多维度评估

### 数据流程

```
问题生成 → 格式转换 → 检索 → 评测
   ↓          ↓         ↓       ↓
 JSON/TXT   CSV      CSV     CSV/JSON
```

### 文件结构和对齐

所有文件使用统一的命名规则，包含 `request_id[:8]` 前缀，确保文件对齐：

- **问题生成模块**：
  - `data/frontend/questions_{timestamp}_{request_id[:8]}.json`
  - `data/backend/questions_{timestamp}_{request_id[:8]}.txt`

- **格式转换模块**：
  - `data/export/questions_{timestamp}_{request_id[:8]}.csv`

- **检索模块**：
  - `data/retrieval/questions_{timestamp}_{request_id[:8]}_with_answers.csv`

- **评测模块**：
  - `data/evaluation/questions_{timestamp}_{request_id[:8]}_with_answers_evaluation_results_{mode}/`
    - `evaluation_results.csv`
    - `evaluation_summary.json`

### 删除功能

格式转换模块的删除功能支持联动删除所有关联文件：
- ✅ JSON 日志文件 (data/frontend/)
- ✅ TXT 日志文件 (data/backend/)
- ✅ CSV 文件 (data/export/) - 格式转换输出
- ✅ 检索 CSV 文件 (data/retrieval/) - 检索模块输出
- ✅ 评测 CSV 和 JSON 文件 (data/evaluation/) - 评测模块输出（包括子目录）
- ✅ 空的评测结果目录

## 快速开始

### 方式一：使用启动脚本（推荐）

```bash
cd gpt-eval-system
chmod +x start.sh
./start.sh
```

这将同时启动后端和前端服务。

### 方式二：手动启动

### 1. 后端设置

#### 1.1 创建虚拟环境（推荐）

```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

#### 1.2 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

#### 1.3 配置环境变量

复制 `env.example` 为 `.env` 并填写配置：

```bash
cd backend
cp env.example .env
```

编辑 `.env` 文件，设置以下变量：

```env
# OpenAI API 配置
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://yiming.zeroerr.team/v1
OPENAI_MODEL=Yiming

# MinIO 配置
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=zero0000
MINIO_BUCKET_NAME=knowledge
MINIO_SECURE=false
```

#### 1.4 运行后端

```bash
cd backend
source venv/bin/activate
uvicorn app:app --reload --host 0.0.0.0 --port 8180
```

后端将在 `http://localhost:8180` 启动。

### 2. 前端设置

#### 2.1 安装依赖

```bash
cd frontend
npm install
```

#### 2.2 运行前端开发服务器

```bash
cd frontend
npm run dev
```

前端将在 `http://localhost:5173` 启动（Vite 默认端口）。

### 3. 访问系统

打开浏览器访问：`http://localhost:5173`

## 使用说明

### 问题生成模块

1. 在首页选择"问题生成"模块
2. 调整每类问题的数量（默认5个）
3. 可以查看和编辑每个分类（S1-S6）的 Prompt
4. 点击"生成问题"按钮
5. 实时查看生成进度和结果
6. 支持搜索、筛选、分页和导出

### 格式转换模块

1. 在首页选择"格式转换"模块
2. 查看可用的日志文件列表
3. 点击"预览"查看CSV内容（支持分页）
4. 点击"转换并下载"将日志转换为CSV格式并下载
5. 点击"删除"可以删除日志文件及其所有关联文件（JSON、TXT、CSV、检索结果、评测结果）

CSV格式包含以下列：
- `question`: 问题文本
- `answer`: 答案（检索模块填充）
- `answer_chapter`: 答案章节（检索模块填充）
- `reference`: 参考章节信息
- `type`: 问题类型（如 "S1 数值问答"、"S2 定义问答"）
- `theme`: 文档来源（如 "eRob"、"eCoder"）
- `retrieved_context`: 检索到的上下文（检索模块填充）

### 检索模块

1. 在首页选择"检索"模块
2. **重新检索**：
   - 选择问题 CSV 文件（格式转换模块的输出）
   - 点击"开始检索"调用 RagFlow API 获取答案
   - 实时查看检索进度
3. **查看已有结果**：
   - 选择检索结果文件（`*_with_answers.csv`）
   - 点击"加载结果"查看历史检索结果
   - 支持分页浏览和下载

检索模块会根据问题类型（S1-S5 vs S6）自动选择不同的 RagFlow Assistant，确保 S6 对抗类问题能够正确响应。

### 评测模块

1. 在首页选择"评测"模块
2. **重新评测**：
   - 选择包含答案的 CSV 文件（检索模块的输出）
   - 选择评测模式（混合评测/章节匹配/Ragas AI）
   - 点击"开始评测"计算评测指标
3. **查看已有结果**：
   - 选择评测结果文件
   - 点击"加载结果"查看历史评测结果
   - 支持下载详细结果 CSV 和摘要 JSON

评测指标包括：
- **答案相关性**：评估答案与问题的相关程度（同时作为用户满意度指标）
- **答案质量**：评估答案的准确性、完整性和一致性
- **章节匹配准确率/召回率**：基于章节信息匹配的传统指标
- **混合评测综合得分**：结合章节匹配（40%）和 Ragas（60%）的综合得分
- **检索成功率**：成功检索到答案的问题比例
- **相关性得分分布**：按等级统计（优秀/良好/一般/较差）
- **按问题类型的相关性得分**：各类型（S1-S6）问题的表现

## API 端点

### 问题生成
- `GET /api/categories` - 获取问题分类列表
- `POST /api/generate` - 生成问题（同步）
- `WebSocket /ws/generate` - 生成问题（实时进度）

### 格式转换
- `GET /api/format/logs` - 列出所有日志文件
- `POST /api/format/convert` - 转换为CSV
- `GET /api/format/download/{log_id}` - 下载CSV文件
- `GET /api/format/check-csv/{log_id}` - 检查CSV文件是否存在
- `DELETE /api/format/logs/{log_id}` - 删除日志文件及其所有关联文件

### 检索
- `GET /api/data/csv-files` - 列出格式转换输出的CSV文件
- `GET /api/data/retrieval-csv-files` - 列出检索输出的CSV文件
- `POST /api/retrieval/run` - 运行检索流程
- `GET /api/retrieval/results` - 列出所有检索结果文件
- `GET /api/retrieval/result` - 获取检索结果详情

### 评测
- `GET /api/data/retrieval-csv-files` - 列出检索输出的CSV文件（评测模块输入）
- `POST /api/evaluation/run` - 运行评测流程
- `GET /api/evaluation/results` - 列出所有评测结果文件
- `GET /api/evaluation/result` - 获取评测结果详情
- `GET /api/evaluation/latest-summary` - 获取最新评测摘要（用于概览页面）

### RagFlow
- `GET /api/ragflow/status` - 检查 RagFlow API 连接状态

### 源文档和问题分析
- `GET /api/source-documents` - 获取知识库文档统计信息
- `GET /api/question-analysis` - 分析问题泛化性

### 完整流程
- `WebSocket /ws/pipeline` - 一键运行完整流程（问题生成 → 格式转换 → 检索 → 评测）

## 文件结构

```
gpt-eval-system/
├── backend/
│   ├── app.py                      # FastAPI 主应用
│   ├── schemas.py                  # 数据模型
│   ├── requirements.txt            # Python 依赖
│   ├── .env                        # 环境变量（需创建）
│   ├── config/
│   │   └── paths.py               # 数据目录配置
│   ├── data/                       # 数据目录（运行时生成）
│   │   ├── frontend/              # JSON 日志文件
│   │   ├── backend/               # TXT 日志文件
│   │   ├── export/                # 格式转换输出的 CSV
│   │   ├── retrieval/             # 检索输出的 CSV（带答案）
│   │   └── evaluation/            # 评测输出的 CSV 和 JSON
│   ├── routers/                    # API 路由
│   │   ├── question_gen_routes.py
│   │   ├── format_convert_routes.py
│   │   ├── retrieval_routes.py
│   │   ├── evaluation_routes.py
│   │   ├── pipeline_routes.py
│   │   └── ragflow_routes.py      # RagFlow API 路由
│   ├── scripts/                    # 工具脚本
│   │   ├── README.md              # 脚本使用指南
│   │   ├── ragflow_import_from_segments.py  # 导入数据到 RagFlow
│   │   ├── ragflow_cleanup.py     # 清理 RagFlow 数据集
│   │   └── ...                    # 其他工具脚本
│   ├── datasets.json              # RagFlow 数据集配置文件
│   └── services/
│       ├── generator.py            # 问题生成逻辑
│       ├── llm_client.py          # LLM API 客户端
│       ├── minio_client.py        # MinIO 客户端
│       ├── templates.py           # Prompt 模板（S1-S6）
│       ├── question_logger.py      # 日志记录
│       ├── format_converter.py    # CSV 转换
│       ├── retrieval_service.py    # 检索服务
│       ├── ragflow_client.py      # RagFlow API 客户端
│       ├── evaluation_service.py  # 评测服务
│       ├── ragas_evaluator.py     # Ragas AI 评测器
│       ├── chapter_matcher.py     # 章节匹配逻辑
│       └── pipeline.py            # 完整流程编排
└── frontend/
    ├── src/
    │   ├── Index.tsx              # 主页面（概览 + 模块导航）
    │   ├── contexts/
    │   │   └── ModuleStateContext.tsx  # 模块状态管理上下文
    │   ├── modules/
    │   │   ├── QuestionGen.tsx    # 问题生成模块
    │   │   ├── FormatConvert.tsx  # 格式转换模块
    │   │   ├── Retrieval.tsx      # 检索模块
    │   │   └── Evaluation.tsx     # 评测模块
    │   └── ...
    └── package.json
```

## 系统工作流程

1. **问题生成**：基于 MinIO 知识库中的文档，从 6 个维度（S1-S6）自动生成多样化的问题
2. **格式转换**：将生成的问题日志转换为标准 CSV 格式，便于后续处理
3. **答案检索**：使用与生产环境相同的 RagFlow API 检索逻辑，获取 GPT 对每个问题的答案
4. **质量评测**：
   - **Ragas AI 评测**：从 LLM 角度评估答案的相关性、质量和忠实度
   - **章节匹配**：基于传统指标评估答案的章节匹配准确率和召回率
   - **混合评测**：综合两种评测方式，提供全面的质量评估报告

## 注意事项

1. **环境变量**：确保 `.env` 文件配置正确，特别是：
   - `OPENAI_API_KEY` - 用于问题生成和 Ragas 评测
   - `OPENAI_BASE_URL` 和 `OPENAI_MODEL` - LLM 配置
   - `RAGFLOW_API_URL` 和 `RAGFLOW_API_KEY` - RagFlow API 配置
   - `RAGFLOW_DATASETS_JSON` - 数据集配置文件路径（可选，默认为 `backend/datasets.json`）
   - MinIO 配置 - 用于问题生成时的知识库访问

2. **datasets.json**：RagFlow 数据集配置文件，位于 `backend/datasets.json`。该文件由脚本自动维护，包含当前 API key 有权限的所有数据集和文档映射。如需手动管理，请参考 `backend/scripts/README.md`。

3. **MinIO 连接**：确保 MinIO 服务运行且 `knowledge` 桶中有文档

4. **RagFlow API**：确保 RagFlow 服务可访问，且已配置数据集。可以使用 `backend/scripts/` 目录下的脚本管理 RagFlow 数据集（详见 `backend/scripts/README.md`）。

5. **端口冲突**：如果 8180 或 5173 端口被占用，需要修改配置

6. **数据目录**：所有数据文件保存在 `backend/data/` 目录下，按模块分类存储

7. **文件对齐**：所有文件使用统一的命名规则（包含 `request_id[:8]` 前缀），确保删除功能能正确匹配所有关联文件

8. **Python 版本**：推荐 Python 3.8+

9. **Node.js 版本**：推荐 Node.js 16+

10. **性能优化**：当前版本为单线程模式，评测速度较慢。大规模评测时请耐心等待，后续版本将支持多并发处理。

## 故障排除

### 后端无法启动
- 检查 Python 版本（推荐 3.8+）
- 检查依赖是否安装完整：`pip install -r requirements.txt`
- 检查 `.env` 文件是否存在且配置正确

### 前端无法连接后端
- 检查后端是否运行在 `http://localhost:8180`
- 检查浏览器控制台是否有 CORS 错误
- 如果后端在不同端口，需要配置 Vite 代理

### 无法生成问题
- 检查 `OPENAI_API_KEY` 是否正确
- 检查 MinIO 连接是否正常
- 查看后端日志输出

## 开发模式

### 后端热重载
后端使用 `uvicorn` 的 `reload=True`，修改代码后会自动重启。

### 前端热重载
前端使用 Vite，修改代码后会自动刷新浏览器。
