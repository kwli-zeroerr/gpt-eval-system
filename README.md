# RAG 评测系统 - 运行指南

## 系统架构

系统包含4个模块：
1. **问题生成模块** - 从6个维度生成测试问题
2. **格式转换模块** - 将问题转换为CSV格式
3. **检索模块** - 批量检索获取答案（待实现）
4. **评测模块** - 评估答案准确性（待实现）

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
3. 点击"转换为CSV"将日志转换为CSV格式
4. 点击"预览"查看CSV内容（前10行）
5. 点击"下载"保存CSV文件

CSV格式包含以下列：
- `question`: 问题文本
- `answer`: 答案（暂时为空）
- `reference`: 章节信息
- `type`: 问题类型（如 "S1 数值问答"）
- `theme`: 文档来源（如 "eRob"、"eCoder"）

## API 端点

### 问题生成
- `GET /api/categories` - 获取问题分类列表
- `POST /api/generate` - 生成问题（同步）
- `WebSocket /ws/generate` - 生成问题（实时进度）

### 格式转换
- `GET /api/format/logs` - 列出所有日志文件
- `POST /api/format/convert` - 转换为CSV
- `GET /api/format/download/{log_id}` - 下载CSV文件

## 文件结构

```
gpt-eval-system/
├── backend/
│   ├── app.py                 # FastAPI 主应用
│   ├── schemas.py             # 数据模型
│   ├── requirements.txt       # Python 依赖
│   ├── .env                   # 环境变量（需创建）
│   ├── logs/                  # 日志文件目录
│   └── services/
│       ├── generator.py       # 问题生成逻辑
│       ├── llm_client.py      # LLM API 客户端
│       ├── minio_client.py    # MinIO 客户端
│       ├── templates.py       # Prompt 模板
│       ├── question_logger.py # 日志记录
│       ├── format_converter.py # CSV 转换
│       └── pipeline.py        # 完整流程编排
└── frontend/
    ├── src/
    │   ├── Index.tsx          # 主页面（模块导航）
    │   ├── modules/
    │   │   ├── QuestionGen.tsx
    │   │   ├── FormatConvert.tsx
    │   │   ├── Retrieval.tsx
    │   │   └── Evaluation.tsx
    │   └── ...
    └── package.json
```

## 注意事项

1. **环境变量**：确保 `.env` 文件配置正确，特别是 `OPENAI_API_KEY` 和 MinIO 配置
2. **MinIO 连接**：确保 MinIO 服务运行且 `knowledge` 桶中有文档
3. **端口冲突**：如果 8180 或 5173 端口被占用，需要修改配置
4. **日志文件**：生成的日志文件保存在 `backend/logs/` 目录
5. **Python 版本**：推荐 Python 3.8+
6. **Node.js 版本**：推荐 Node.js 16+

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
