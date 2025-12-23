import { useEffect, useState } from "react";
import { useModuleState } from "../contexts/ModuleStateContext";

interface RetrievalResult {
  output_csv_path: string;
  input_csv_path: string;
  total_questions: number;
  completed: number;
  failed: number;
  total_time: number;
}

interface RetrievalItem {
  question: string;
  answer: string;
  reference: string;
  type: string;
  theme: string;
}

interface CsvFile {
  path: string;
  filename: string;
  size: number;
  modified_at: string;
}

interface RetrievalResultFile {
  path: string;
  filename: string;
  size: number;
  modified_at: number;
}

function Retrieval() {
  const { getModuleState, setModuleState } = useModuleState();
  const moduleId = "retrieval";
  
  // 从状态管理恢复状态
  const savedState = getModuleState(moduleId) || {};
  
  const [csvFiles, setCsvFiles] = useState<CsvFile[]>([]);
  const [selectedCsv, setSelectedCsv] = useState<string>(savedState.selectedCsv || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RetrievalResult | null>(savedState.result || null);
  const [progress, setProgress] = useState({ current: 0, total: 0, percentage: 0 });
  const [items, setItems] = useState<RetrievalItem[]>(savedState.items || []);
  const [currentPage, setCurrentPage] = useState(savedState.currentPage || 1);
  const [resultFiles, setResultFiles] = useState<RetrievalResultFile[]>([]);
  const [selectedResultFile, setSelectedResultFile] = useState<string>(savedState.selectedResultFile || "");
  const [viewMode, setViewMode] = useState<"new" | "existing">(savedState.viewMode || "new");
  const [pageSize, setPageSize] = useState(savedState.pageSize || 10);
  
  // 保存状态到状态管理
  useEffect(() => {
    setModuleState(moduleId, {
      selectedCsv,
      result,
      items,
      currentPage,
      selectedResultFile,
      viewMode,
      pageSize,
    });
  }, [selectedCsv, result, items, currentPage, selectedResultFile, viewMode, pageSize, setModuleState]);

  const handleLoadResult = async () => {
    if (!selectedResultFile) {
      setError("请选择检索结果文件");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const detailResp = await fetch(
        `/api/retrieval/result?csv_path=${encodeURIComponent(selectedResultFile)}`
      );
      if (detailResp.ok) {
        const detailData = await detailResp.json();
        setItems(detailData.items || []);
        setCurrentPage(1);
        setResult({
          output_csv_path: selectedResultFile,
          input_csv_path: "",
          total_questions: detailData.total || 0,
          completed: detailData.items?.filter((item: RetrievalItem) => item.answer).length || 0,
          failed: detailData.items?.filter((item: RetrievalItem) => !item.answer).length || 0,
          total_time: 0,
        });
      } else {
        throw new Error("加载检索结果失败");
      }
    } catch (e) {
      setError("加载检索结果失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCsvFiles();
    fetchResultFiles();
    
    // 监听管道完成事件，自动加载最新检索结果
    const handlePipelineComplete = async (event: CustomEvent) => {
      const { csv_path } = event.detail;
      if (!csv_path) return;
      
      // 等待一下确保文件已经生成
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // 刷新结果文件列表
      await fetchResultFiles();
      
      // 设置选中的结果文件并加载
      setSelectedResultFile(csv_path);
      setViewMode('view-result');
      
      // 加载结果
      try {
        const detailResp = await fetch(
          `/api/retrieval/result?csv_path=${encodeURIComponent(csv_path)}`
        );
        if (detailResp.ok) {
          const detailData = await detailResp.json();
          setItems(detailData.items || []);
          setCurrentPage(1);
          setResult({
            output_csv_path: csv_path,
            input_csv_path: "",
            total_questions: detailData.total || 0,
            completed: detailData.items?.filter((item: RetrievalItem) => item.answer).length || 0,
            failed: detailData.items?.filter((item: RetrievalItem) => !item.answer).length || 0,
            total_time: 0,
          });
        }
      } catch (e) {
        console.error("自动加载检索结果失败", e);
      }
    };
    
    window.addEventListener('pipeline-complete-retrieval', handlePipelineComplete as EventListener);
    
    return () => {
      window.removeEventListener('pipeline-complete-retrieval', handlePipelineComplete as EventListener);
    };
  }, []);

  const fetchResultFiles = async () => {
    try {
      const response = await fetch("/api/retrieval/results");
      const data = await response.json();
      const files = (data.result_files || []).sort(
        (a: RetrievalResultFile, b: RetrievalResultFile) => (b.modified_at || 0) - (a.modified_at || 0)
      );
      setResultFiles(files);

      // 默认选中最新的结果文件（如果没有已选中的）
      if (files.length > 0 && !selectedResultFile) {
        setSelectedResultFile(files[0].path);
      }
      if (files.length > 0 && !selectedResultFile) {
        setSelectedResultFile(files[0].path);
      }
    } catch (e) {
      console.error("获取检索结果文件列表失败", e);
    }
  };

  const fetchCsvFiles = async () => {
    try {
      const response = await fetch("/api/data/csv-files");
      const data = await response.json();
      const files = (data.csv_files || []).sort(
        (a: CsvFile, b: CsvFile) => new Date(b.modified_at).getTime() - new Date(a.modified_at).getTime()
      );
      setCsvFiles(files);

      // 默认选中最新的 CSV
      if (files.length > 0 && !selectedCsv) {
        setSelectedCsv(files[0].path);
      }
    } catch (e) {
      setError("获取 CSV 文件列表失败");
    }
  };

  const handleRun = async () => {
    if (!selectedCsv) {
      setError("请选择 CSV 文件");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setItems([]);
    setCurrentPage(1);
    setProgress({ current: 0, total: 0, percentage: 0 });

    try {
      const response = await fetch("/api/retrieval/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv_path: selectedCsv }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "检索失败");
      }

      const data: RetrievalResult = await response.json();
      setResult(data);

      if (data.output_csv_path) {
        try {
          const detailResp = await fetch(
            `/api/retrieval/result?csv_path=${encodeURIComponent(data.output_csv_path)}`
          );
          if (detailResp.ok) {
            const detailData = await detailResp.json();
            setItems(detailData.items || []);
            setCurrentPage(1);
          }
        } catch (err) {
          console.error("加载检索明细失败", err);
        }
      }

      await fetchCsvFiles();
    } catch (e) {
      setError("检索失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const downloadResult = () => {
    if (result?.output_csv_path) {
      window.open(result.output_csv_path, "_blank");
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  };

  const formatTime = (seconds: number) => {
    if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`;
    return `${seconds.toFixed(2)} s`;
  };

  return (
    <div className="retrieval-module">
      <h2>检索模块</h2>
      <p>从 CSV 文件读取问题，调用 RagFlow API 检索相关文档片段</p>

      {error && (
        <div className="alert alert-error">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="2"/>
            <path d="M10 6v4M10 14h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          <span>{error}</span>
        </div>
      )}

      <div className="retrieval-actions">
        {/* 重新检索 */}
        <div className="retrieval-form">
          <h3>重新检索</h3>
          <div className="form-group">
            <label htmlFor="csv-select">选择问题 CSV 文件</label>
            {csvFiles.length === 0 ? (
              <div className="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10 9 9 9 8 9"/>
                </svg>
                <p>暂无可用 CSV 文件</p>
                <p className="empty-state-hint">请先在"格式转换"模块生成 CSV 文件</p>
              </div>
            ) : (
              <select
                id="csv-select"
                value={selectedCsv}
                onChange={(e) => setSelectedCsv(e.target.value)}
                disabled={loading}
                className="form-select"
              >
                <option value="">-- 请选择 CSV 文件 --</option>
                {csvFiles.map((file) => (
                  <option key={file.path} value={file.path}>
                    {file.filename} ({formatFileSize(file.size)}, {new Date(file.modified_at).toLocaleString()})
                  </option>
                ))}
              </select>
            )}
          </div>

          <button
            onClick={handleRun}
            disabled={loading || !selectedCsv}
            className="btn-retrieval-action"
          >
            {loading ? (
              <>
                <svg className="spinner" width="18" height="18" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" strokeDasharray="32" strokeDashoffset="32">
                    <animate attributeName="stroke-dasharray" dur="2s" values="0 32;16 16;0 32;0 32" repeatCount="indefinite"/>
                    <animate attributeName="stroke-dashoffset" dur="2s" values="0;-16;-32;-32" repeatCount="indefinite"/>
                  </circle>
                </svg>
                <span>检索中...</span>
              </>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <circle cx="11" cy="11" r="8"/>
                  <path d="m21 21-4.35-4.35"/>
                </svg>
                <span>开始检索</span>
              </>
            )}
          </button>
        </div>

        {/* 查看已有结果 */}
        <div className="retrieval-form">
          <h3>查看已有结果</h3>
          <div className="form-group">
            <label htmlFor="result-select">选择检索结果文件</label>
            {resultFiles.length === 0 ? (
              <div className="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10 9 9 9 8 9"/>
                </svg>
                <p>暂无检索结果文件</p>
                <p className="empty-state-hint">请先运行检索生成结果</p>
              </div>
            ) : (
              <select
                id="result-select"
                value={selectedResultFile}
                onChange={(e) => setSelectedResultFile(e.target.value)}
                disabled={loading}
                className="form-select"
              >
                <option value="">-- 请选择检索结果文件 --</option>
                {resultFiles.map((file) => (
                  <option key={file.path} value={file.path}>
                    {file.filename} ({formatFileSize(file.size)}, {new Date(file.modified_at * 1000).toLocaleString()})
                  </option>
                ))}
              </select>
            )}
          </div>

          <button
            onClick={handleLoadResult}
            disabled={loading || !selectedResultFile}
            className="btn-retrieval-action"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <span>加载结果</span>
          </button>
        </div>
      </div>

      {loading && progress.total > 0 && (
        <div className="card progress-card">
          <div className="progress-header">
            <span className="progress-label">检索进度</span>
            <span className="progress-percentage">{progress.percentage}%</span>
          </div>
          <div className="progress-bar-modern">
            <div
              className="progress-fill-modern"
              style={{ width: `${progress.percentage}%` }}
            />
          </div>
          <p className="progress-text">
            {progress.current} / {progress.total} 个问题
          </p>
        </div>
      )}

      {result && items.length > 0 && (
        <div className="csv-preview">
          <div className="csv-preview-header">
            <h3>检索结果预览</h3>
            <div className="csv-preview-controls">
              <label>
                每页显示：
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setCurrentPage(1);
                  }}
                  className="rows-per-page-select"
                >
                  <option value={10}>10 行</option>
                  <option value={20}>20 行</option>
                  <option value={50}>50 行</option>
                  <option value={100}>100 行</option>
                </select>
              </label>
              <span className="csv-total-rows">
                共 {items.length} 条结果（成功: {result.completed}, 失败: {result.failed}, 耗时: {formatTime(result.total_time)}）
              </span>
              <button onClick={downloadResult} className="btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                下载 CSV
              </button>
            </div>
          </div>
          
          {/* Pagination */}
          {items.length > pageSize && (
            <div className="csv-pagination">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="page-btn"
              >
                上一页
              </button>
              <span className="page-info">
                第 {currentPage} / {Math.ceil(items.length / pageSize)} 页
              </span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(Math.ceil(items.length / pageSize), p + 1))}
                disabled={currentPage >= Math.ceil(items.length / pageSize)}
                className="page-btn"
              >
                下一页
              </button>
            </div>
          )}
          
          <div className="csv-table-container">
            <table className="csv-table">
              <thead>
                <tr>
                  <th>题目</th>
                  <th>检索答案</th>
                  <th>参考章节</th>
                  <th>类型</th>
                  <th>主题</th>
                </tr>
              </thead>
              <tbody>
                {items
                  .slice((currentPage - 1) * pageSize, currentPage * pageSize)
                  .map((item, idx) => (
                    <tr key={`${currentPage}-${idx}`}>
                      <td className="cell-question">{item.question}</td>
                      <td className="cell-answer">
                        {item.answer ? (
                          <div 
                            className="answer-markdown" 
                            dangerouslySetInnerHTML={{ 
                              __html: item.answer
                                // 代码块（```code```）
                                .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
                                // 行内代码（`code`）
                                .replace(/`([^`\n]+)`/g, '<code>$1</code>')
                                // 粗体（**text**）
                                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                                // 斜体（*text*）
                                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                                // 标题（### text）
                                .replace(/^### (.*$)/gm, '<h3>$1</h3>')
                                .replace(/^## (.*$)/gm, '<h2>$1</h2>')
                                .replace(/^# (.*$)/gm, '<h1>$1</h1>')
                                // 无序列表（- item）
                                .replace(/^- (.*$)/gm, '<li>$1</li>')
                                // 有序列表（1. item）
                                .replace(/^\d+\. (.*$)/gm, '<li>$1</li>')
                                // 链接（[text](url)）
                                .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
                                // 换行
                                .replace(/\n/g, '<br/>')
                                // 包装列表项
                                .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
                            }} 
                          />
                        ) : (
                          <span className="answer-empty">-</span>
                        )}
                      </td>
                      <td className="cell-reference">
                        {item.reference ? (
                          <span className="reference-text">{item.reference}</span>
                        ) : (
                          <span className="answer-empty">-</span>
                        )}
                      </td>
                      <td>
                        <span className="type-badge">{item.type || "-"}</span>
                      </td>
                      <td className="cell-theme">
                        {item.theme ? (
                          <span className="theme-text">{item.theme}</span>
                        ) : (
                          <span className="answer-empty">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default Retrieval;
