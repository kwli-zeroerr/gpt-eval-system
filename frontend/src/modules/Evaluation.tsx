import { useEffect, useState } from "react";

type EvaluationMode = "chapter_match" | "ragas" | "hybrid";

// Tooltip 组件
const MetricTooltip = ({ text }: { text: string }) => {
  const [showTooltip, setShowTooltip] = useState(false);
  
  return (
    <span className="metric-tooltip-wrapper">
      <span 
        className="metric-info-icon"
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="16" x2="12" y2="12"/>
          <line x1="12" y1="8" x2="12.01" y2="8"/>
        </svg>
      </span>
      {showTooltip && (
        <div className="metric-tooltip">
          {text}
        </div>
      )}
    </span>
  );
};

interface EvaluationResult {
  results_csv_path: string;
  summary_json_path: string;
  summary: {
    total_questions: number;
    mode: EvaluationMode;
    // 章节匹配指标
    chapter_match_correct_count?: number;
    chapter_match_accuracy?: number;
    chapter_match_recall?: number;
    chapter_match_accuracy_percentage?: number;
    chapter_match_recall_percentage?: number;
    // Ragas 指标
    ragas_overall_score?: number;
    ragas_overall_score_percentage?: number;
    ragas_quality_score?: number;
    ragas_relevancy_score?: number;
    ragas_satisfaction_score?: number;
    // Ragas 状态
    ragas_available?: boolean;
    ragas_init_error?: string;
    // 混合模式指标
    hybrid_score?: number;
    hybrid_score_percentage?: number;
    hybrid_correct_count?: number;
    // 向后兼容的旧字段
    correct_count?: number;
    accuracy?: number;
    recall?: number;
    accuracy_percentage?: number;
    recall_percentage?: number;
    // 检索优化指标
    recall_at_3?: number;
    recall_at_3_percentage?: number;
    recall_at_5?: number;
    recall_at_5_percentage?: number;
    recall_at_10?: number;
    recall_at_10_percentage?: number;
    // 性能指标
    avg_retrieval_time?: number;
    p50_retrieval_time?: number;
    p95_retrieval_time?: number;
    avg_generation_time?: number;
    p50_generation_time?: number;
    p95_generation_time?: number;
    avg_total_time?: number;
    p50_total_time?: number;
    p95_total_time?: number;
    concurrent_10_avg_time?: number;
    // 泛化性指标
    generalization_score?: number;
    generalization_score_percentage?: number;
    S1_avg_score?: number;
    S2_avg_score?: number;
    S3_avg_score?: number;
    S4_avg_score?: number;
    S5_avg_score?: number;
    S6_avg_score?: number;
    // 优化建议
    optimization_suggestions?: Array<{
      category: string;
      metric: string;
      current_value: string;
      suggestion: string;
    }>;
  };
  total_questions: number;
  total_time: number;
  mode: EvaluationMode;
}

interface CsvFile {
  path: string;
  filename: string;
  size: number;
  modified_at: string;
}

interface EvaluationResultFile {
  summary_path: string;
  csv_path: string | null;
  filename: string;
  size: number;
  modified_at: number;
  mode: EvaluationMode;
  total_questions: number;
}

function Evaluation() {
  const [csvFiles, setCsvFiles] = useState<CsvFile[]>([]);
  const [selectedCsv, setSelectedCsv] = useState<string>("");
  const [evaluationMode, setEvaluationMode] = useState<EvaluationMode>("hybrid");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [progress, setProgress] = useState({ current: 0, total: 0, percentage: 0 });
  const [resultFiles, setResultFiles] = useState<EvaluationResultFile[]>([]);
  const [selectedResultFile, setSelectedResultFile] = useState<string>("");
  const [viewMode, setViewMode] = useState<"new" | "existing">("new");
  const [perQuestionItems, setPerQuestionItems] = useState<any[]>([]);
  const [itemPage, setItemPage] = useState(1);
  const [itemPageSize, setItemPageSize] = useState(10);

  useEffect(() => {
    fetchCsvFiles();
    fetchResultFiles();
    
    // 监听管道完成事件，自动刷新文件列表并加载最新评测结果
    const handlePipelineComplete = async (event: CustomEvent) => {
      const { summary_path } = event.detail;
      if (!summary_path) return;
      
      // 等待一下确保文件已经生成
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // 刷新文件列表
      await fetchCsvFiles();
      await fetchResultFiles();
      
      // 设置选中的结果文件并加载
      setSelectedResultFile(summary_path);
      setViewMode('existing');
      
      // 加载结果
      await loadDetail(summary_path);
    };
    
    window.addEventListener('pipeline-complete-evaluation', handlePipelineComplete as EventListener);
    
    return () => {
      window.removeEventListener('pipeline-complete-evaluation', handlePipelineComplete as EventListener);
    };
  }, []);

  const fetchCsvFiles = async () => {
    try {
      // 评估模块从 data/retrieval/ 读取 CSV（检索的输出，包含答案）
      const response = await fetch("/api/data/retrieval-csv-files");
      const data = await response.json();
      const files = (data.csv_files || []).sort(
        (a: CsvFile, b: CsvFile) => new Date(b.modified_at).getTime() - new Date(a.modified_at).getTime()
      );
      setCsvFiles(files);
      if (files.length > 0 && !selectedCsv) {
        setSelectedCsv(files[0].path);
      }
    } catch (e) {
      setError("获取 CSV 文件列表失败");
    }
  };

  const fetchResultFiles = async () => {
    try {
      const response = await fetch("/api/evaluation/results");
      const data = await response.json();
      const files = (data.result_files || []).sort(
        (a: EvaluationResultFile, b: EvaluationResultFile) => (b.modified_at || 0) - (a.modified_at || 0)
      );
      setResultFiles(files);
      if (files.length > 0 && !selectedResultFile) {
        setSelectedResultFile(files[0].summary_path);
      }
    } catch (e) {
      console.error("获取评测结果文件列表失败", e);
    }
  };

  const handleLoadResult = async () => {
    if (!selectedResultFile) {
      setError("请选择评测结果文件");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      await loadDetail(selectedResultFile);
    } catch (e) {
      setError("加载评测结果失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const handleRun = async () => {
    if (!selectedCsv) {
      setError("请选择包含答案的 CSV 文件");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setProgress({ current: 0, total: 0, percentage: 0 });

    try {
      const response = await fetch("/api/evaluation/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          csv_path: selectedCsv,
          mode: evaluationMode
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "评测失败");
      }

      const data = await response.json();
      setResult(data);
      // 运行完成后加载明细（最新文件）
      if (data.summary_json_path) {
        await loadDetail(data.summary_json_path);
      } else {
        setPerQuestionItems([]);
      }
      await fetchResultFiles();
    } catch (e) {
      setError("评测失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const downloadResults = () => {
    if (result?.results_csv_path) {
      window.open(result.results_csv_path, "_blank");
    }
  };

  const loadDetail = async (summaryPath: string) => {
    try {
      const detailResp = await fetch(
        `/api/evaluation/result?summary_path=${encodeURIComponent(summaryPath)}`
      );
      if (!detailResp.ok) {
        throw new Error("加载评测结果失败");
      }
      const detailData = await detailResp.json();
      setResult({
        results_csv_path: detailData.results_csv_path || "",
        summary_json_path: detailData.summary_json_path || "",
        summary: detailData.summary || {},
        total_questions: detailData.total_questions || 0,
        total_time: detailData.total_time || 0,
        mode: detailData.mode || "hybrid",
      });
      setPerQuestionItems(detailData.items || []);
      setItemPage(1);
    } catch (err) {
      console.error("加载评测明细失败", err);
      setError("加载评测结果失败: " + (err instanceof Error ? err.message : String(err)));
    }
  };

  const downloadSummary = () => {
    if (result?.summary_json_path) {
      window.open(result.summary_json_path, "_blank");
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  };

  const formatScore = (v: any, toPercent = true) => {
    if (v === null || v === undefined || v === "") return "-";
    const num = Number(v);
    if (Number.isNaN(num)) return v;
    return toPercent ? `${(num * 100).toFixed(2)}%` : num.toFixed(2);
  };

  const getModeDescription = (mode: EvaluationMode): string => {
    switch (mode) {
      case "chapter_match":
        return "章节匹配评测（传统方法，基于章节信息匹配）";
      case "ragas":
        return "Ragas AI 评测（使用 AI 评估答案质量、相关性等）";
      case "hybrid":
        return "混合评测（章节匹配 + Ragas AI，综合评估）";
      default:
        return "";
    }
  };

  return (
    <div className="evaluation-module">
      <h2>评测模块</h2>
      <p>从带答案的 CSV 文件计算评测指标（准确率、召回率、用户满意度等）</p>

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
        {/* 重新评测 */}
        <div className="retrieval-form">
          <h3>重新评测</h3>
        <div className="form-group">
            <label htmlFor="csv-select">选择包含答案的 CSV 文件</label>
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
                <p className="empty-state-hint">请先在"检索"模块运行检索，生成带答案的 CSV 文件</p>
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

          <div className="form-group">
            <label htmlFor="mode-select">评测模式</label>
            <select
              id="mode-select"
              value={evaluationMode}
              onChange={(e) => setEvaluationMode(e.target.value as EvaluationMode)}
              disabled={loading}
              className="form-select"
            >
              <option value="hybrid">混合评测（推荐）</option>
              <option value="chapter_match">章节匹配评测</option>
              <option value="ragas">Ragas AI 评测</option>
            </select>
            <p className="form-hint">{getModeDescription(evaluationMode)}</p>
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
                <span>评测中...</span>
              </>
            ) : (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                  <polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
                <span>开始评测</span>
              </>
            )}
          </button>
        </div>

        {/* 查看已有结果 */}
        <div className="retrieval-form">
          <h3>查看已有结果</h3>
          <div className="form-group">
            <label htmlFor="result-select">选择评测结果文件</label>
            {resultFiles.length === 0 ? (
              <div className="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10 9 9 9 8 9"/>
                </svg>
                <p>暂无评测结果文件</p>
                <p className="empty-state-hint">请先运行评测生成结果</p>
              </div>
            ) : (
              <select
                id="result-select"
                value={selectedResultFile}
                onChange={(e) => setSelectedResultFile(e.target.value)}
                disabled={loading}
                className="form-select"
              >
                <option value="">-- 请选择评测结果文件 --</option>
                {resultFiles.map((file) => (
                  <option key={file.summary_path} value={file.summary_path}>
                    {file.filename} ({formatFileSize(file.size)}, {new Date(file.modified_at * 1000).toLocaleString()}, {file.mode}, {file.total_questions} 问题)
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
            <span className="progress-label">评测进度</span>
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

      {result && (
        <div className="evaluation-result">
          <div className="csv-preview-header">
          <h3>评测结果</h3>
            <div className="csv-preview-controls">
              <button onClick={downloadResults} className="btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                下载详细结果 CSV
              </button>
              <button onClick={downloadSummary} className="btn-secondary" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                下载摘要 JSON
              </button>
            </div>
          </div>

          {/* 核心指标 - 精简版 */}
          <div className="dashboard-section">
            <div className="section-header">
              <h3>核心指标</h3>
              <p className="section-subtitle">
                {result.mode === "hybrid" ? "混合评测" : result.mode === "ragas" ? "Ragas AI 评测" : "章节匹配评测"} · 
                共 {result.summary.total_questions} 个问题 · 
                耗时 {result.total_time.toFixed(2)} 秒
              </p>
            </div>
            
            <div className="dashboard-metrics">
              {/* 综合得分 - 最重要指标 */}
              {result.mode === "hybrid" && result.summary.hybrid_score_percentage !== undefined && (
                <div className="metric-card apple-style" style={{ border: "2px solid #007AFF", background: "linear-gradient(135deg, #f0f9ff 0%, #ffffff 100%)" }}>
                  <div className="metric-label">
                    综合得分
                    <MetricTooltip text="混合评测的综合得分，结合章节匹配准确率（权重40%）和Ragas综合得分（权重60%）计算。" />
                  </div>
                  <div className="metric-value" style={{ color: "#007AFF" }}>
                    {result.summary.hybrid_score_percentage.toFixed(2)}%
                  </div>
                </div>
              )}
              {result.mode !== "hybrid" && result.summary.ragas_overall_score_percentage !== undefined && (
                <div className="metric-card apple-style" style={{ border: "2px solid #007AFF", background: "linear-gradient(135deg, #f0f9ff 0%, #ffffff 100%)" }}>
                  <div className="metric-label">
                    综合得分
                    <MetricTooltip text="Ragas AI 评测的综合得分，基于答案质量、相关性等指标的平均值计算。" />
                  </div>
                  <div className="metric-value" style={{ color: "#007AFF" }}>
                    {result.summary.ragas_overall_score_percentage?.toFixed(2) || "0.00"}%
                  </div>
                </div>
              )}
              
              {/* 答案相关性 */}
              {result.summary.ragas_relevancy_score_percentage !== undefined && (
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    答案相关性
                    <MetricTooltip text="评估答案与问题的相关程度，反映系统回答是否直接、准确地解决了用户问题。该指标同时作为用户满意度的衡量标准。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_relevancy_score_percentage?.toFixed(2) || "0.00"}%
                  </div>
                </div>
              )}
              
              {/* 答案质量 */}
              {result.summary.ragas_quality_score_percentage !== undefined && (
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    答案质量
                    <MetricTooltip text="评估答案的准确性、完整性和一致性，反映答案的整体质量水平。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_quality_score_percentage?.toFixed(2) || "0.00"}%
                  </div>
                </div>
              )}
              
              {/* 章节匹配准确率 */}
              {(result.summary.chapter_match_accuracy_percentage !== undefined || result.summary.accuracy_percentage !== undefined) && (
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    章节匹配准确率
                    <MetricTooltip text="基于章节信息匹配的传统准确率指标，反映答案与参考章节的匹配程度。正确匹配的答案数占总问题数的比例。" />
                  </div>
                  <div className="metric-value">
                    {(result.summary.chapter_match_accuracy_percentage ?? result.summary.accuracy_percentage ?? 0).toFixed(2)}%
                  </div>
                </div>
              )}
              
              {/* 检索成功率 */}
              {result.summary.retrieval_success_rate_percentage !== undefined && (
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    检索成功率
                    <MetricTooltip text="成功检索到答案的问题占总问题的比例，反映检索系统的可用性和稳定性。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.retrieval_success_rate_percentage.toFixed(2)}%
                  </div>
                </div>
              )}
            </div>
            
            {/* Ragas 不可用警告 */}
            {result.summary.ragas_available === false && (
              <div className="alert alert-warning" style={{ marginTop: "24px" }}>
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <path d="M10 2L2 18h16L10 2z" stroke="currentColor" strokeWidth="2" fill="none"/>
                  <path d="M10 8v4M10 14h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                </svg>
                <div>
                  <strong>Ragas 评测不可用</strong>
                  <p style={{ margin: "5px 0 0 0", fontSize: "0.9em" }}>
                    {result.summary.ragas_init_error || "Ragas 评测器初始化失败，可能缺少 OPENAI_API_KEY 环境变量"}
                  </p>
                  <p style={{ margin: "5px 0 0 0", fontSize: "0.85em", opacity: 0.8 }}>
                    请检查后端环境变量配置，确保 OPENAI_API_KEY 已正确设置。
                  </p>
                </div>
              </div>
            )}
          </div>
          
          {/* 检索优化指标 */}
          {(result.summary.recall_at_3 !== undefined || result.summary.recall_at_5 !== undefined || result.summary.recall_at_10 !== undefined) && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>检索优化指标</h3>
                <p className="section-subtitle">用于优化检索系统（嵌入模型、检索策略）</p>
              </div>
              <div className="dashboard-metrics">
                {result.summary.recall_at_3 !== undefined && (
                  <div className="metric-card apple-style" style={{ border: "2px solid #34C759", background: "linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%)" }}>
                    <div className="metric-label">
                      召回率@3
                      <MetricTooltip text="前3个检索结果中包含正确答案的比例。用于评估检索系统的精确性，值越高表示检索越精准。" />
                    </div>
                    <div className="metric-value" style={{ color: "#34C759" }}>
                      {formatScore(result.summary.recall_at_3)}
                    </div>
                  </div>
                )}
                {result.summary.recall_at_5 !== undefined && (
                  <div className="metric-card apple-style" style={{ border: "2px solid #34C759", background: "linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%)" }}>
                    <div className="metric-label">
                      召回率@5
                      <MetricTooltip text="前5个检索结果中包含正确答案的比例。用于评估检索系统的召回能力，值越高表示检索覆盖越全面。" />
                    </div>
                    <div className="metric-value" style={{ color: "#34C759" }}>
                      {formatScore(result.summary.recall_at_5)}
                    </div>
                  </div>
                )}
                {result.summary.recall_at_10 !== undefined && (
                  <div className="metric-card apple-style" style={{ border: "2px solid #34C759", background: "linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%)" }}>
                    <div className="metric-label">
                      召回率@10
                      <MetricTooltip text="前10个检索结果中包含正确答案的比例。用于评估检索系统的整体召回能力，是检索优化的关键指标。" />
                    </div>
                    <div className="metric-value" style={{ color: "#34C759" }}>
                      {formatScore(result.summary.recall_at_10)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
          
          {/* 提示词优化指标 */}
          {(result.summary.ragas_relevancy_score !== undefined || result.summary.ragas_quality_score !== undefined) && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>提示词优化指标</h3>
                <p className="section-subtitle">用于优化提示词工程</p>
              </div>
              <div className="dashboard-metrics">
                {result.summary.ragas_relevancy_score !== undefined && (
                  <div className="metric-card apple-style" style={{ border: "2px solid #FF9500", background: "linear-gradient(135deg, #fff7ed 0%, #ffffff 100%)" }}>
                    <div className="metric-label">
                      相关性得分
                      <MetricTooltip text="答案与问题的相关程度，用于评估提示词是否能够引导模型生成相关答案。得分低时建议优化提示词。" />
                    </div>
                    <div className="metric-value" style={{ color: "#FF9500" }}>
                      {formatScore(result.summary.ragas_relevancy_score)}
                    </div>
                  </div>
                )}
                {result.summary.ragas_quality_score !== undefined && (
                  <div className="metric-card apple-style" style={{ border: "2px solid #FF9500", background: "linear-gradient(135deg, #fff7ed 0%, #ffffff 100%)" }}>
                    <div className="metric-label">
                      答案质量得分
                      <MetricTooltip text="答案的准确性、完整性和一致性，用于评估提示词是否能够引导模型生成高质量答案。得分低时建议优化提示词。" />
                    </div>
                    <div className="metric-value" style={{ color: "#FF9500" }}>
                      {formatScore(result.summary.ragas_quality_score)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
          
          {/* 系统性能指标 */}
          {(result.summary.avg_retrieval_time !== undefined || result.summary.avg_generation_time !== undefined || result.summary.avg_total_time !== undefined) && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>系统性能指标</h3>
                <p className="section-subtitle">响应时间和并发性能</p>
              </div>
              <div className="dashboard-metrics">
                {result.summary.avg_retrieval_time !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      平均检索时间
                      <MetricTooltip text="单次检索API调用的平均耗时（秒），反映检索系统的响应速度。" />
                    </div>
                    <div className="metric-value">
                      {result.summary.avg_retrieval_time.toFixed(3)}s
                    </div>
                    {result.summary.p95_retrieval_time !== undefined && (
                      <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                        P95: {result.summary.p95_retrieval_time.toFixed(3)}s
                      </div>
                    )}
                  </div>
                )}
                {result.summary.avg_generation_time !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      平均生成时间
                      <MetricTooltip text="Completion API调用的平均耗时（秒），反映生成系统的响应速度。" />
                    </div>
                    <div className="metric-value">
                      {result.summary.avg_generation_time.toFixed(3)}s
                    </div>
                    {result.summary.p95_generation_time !== undefined && (
                      <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                        P95: {result.summary.p95_generation_time.toFixed(3)}s
                      </div>
                    )}
                  </div>
                )}
                {result.summary.avg_total_time !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      平均总响应时间
                      <MetricTooltip text="检索+生成的总平均耗时（秒），反映系统整体响应速度。" />
                    </div>
                    <div className="metric-value">
                      {result.summary.avg_total_time.toFixed(3)}s
                    </div>
                    {result.summary.p95_total_time !== undefined && (
                      <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                        P95: {result.summary.p95_total_time.toFixed(3)}s
                      </div>
                    )}
                    {result.summary.concurrent_10_avg_time !== undefined && (
                      <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                        并发10条: {result.summary.concurrent_10_avg_time.toFixed(3)}s
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
          
          {/* 泛化性分析 */}
          {result.summary.generalization_score !== undefined && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>泛化性分析</h3>
                <p className="section-subtitle">系统对不同类型问题的处理能力均衡度</p>
              </div>
              <div className="dashboard-metrics">
                <div className="metric-card apple-style" style={{ border: "2px solid #AF52DE", background: "linear-gradient(135deg, #faf5ff 0%, #ffffff 100%)" }}>
                  <div className="metric-label">
                    泛化性得分
                    <MetricTooltip text="评估系统对不同类型问题（S1-S6）的处理能力均衡度。得分越高，说明系统对各种问题的处理能力越均衡，泛化能力越强。" />
                  </div>
                  <div className="metric-value" style={{ color: "#AF52DE" }}>
                    {formatScore(result.summary.generalization_score)}
                  </div>
                </div>
                {/* 各类型问题得分 */}
                {["S1", "S2", "S3", "S4", "S5", "S6"].map((type) => {
                  const avgScore = result.summary[`${type}_avg_score`];
                  if (avgScore === undefined) return null;
                  
                  const typeNames: Record<string, string> = {
                    "S1": "数值问答",
                    "S2": "定义问答",
                    "S3": "多选题",
                    "S4": "单文件多段",
                    "S5": "多文件多段",
                    "S6": "对抗数据/敏感信息"
                  };
                  
                  return (
                    <div key={type} className="metric-card apple-style">
                      <div className="metric-label">
                        {type} 平均得分
                        <MetricTooltip text={`${typeNames[type] || type}类型问题的平均得分`} />
                      </div>
                      <div className="metric-value">
                        {formatScore(avgScore)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          
          {/* 优化建议 */}
          {result.summary.optimization_suggestions && result.summary.optimization_suggestions.length > 0 && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>优化建议</h3>
                <p className="section-subtitle">根据评测结果自动生成的优化建议</p>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                {result.summary.optimization_suggestions.map((suggestion: any, idx: number) => (
                  <div key={idx} className="alert" style={{ 
                    backgroundColor: suggestion.category === "提示词优化" ? "#fff7ed" : 
                                    suggestion.category === "检索优化" ? "#f0fdf4" : "#faf5ff",
                    border: `1px solid ${suggestion.category === "提示词优化" ? "#FF9500" : 
                                          suggestion.category === "检索优化" ? "#34C759" : "#AF52DE"}`,
                    padding: "16px",
                    borderRadius: "12px"
                  }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
                      <div style={{ 
                        padding: "4px 8px", 
                        borderRadius: "6px", 
                        fontSize: "12px", 
                        fontWeight: 600,
                        backgroundColor: suggestion.category === "提示词优化" ? "#FF9500" : 
                                        suggestion.category === "检索优化" ? "#34C759" : "#AF52DE",
                        color: "white"
                      }}>
                        {suggestion.category}
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                          {suggestion.metric}: {suggestion.current_value}
                        </div>
                        <div style={{ fontSize: "14px", opacity: 0.8 }}>
                          {suggestion.suggestion}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
            
          {/* 相关性得分分布 - 卡片形式，同一层级 */}
          {result.summary.ragas_relevancy_excellent_count !== undefined && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>相关性得分分布</h3>
                <p className="section-subtitle">按得分等级统计</p>
              </div>
              <div className="dashboard-metrics">
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    优秀 (≥80%)
                    <MetricTooltip text="答案相关性得分在80%以上的问题数量，表示答案与问题高度相关，用户满意度高。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_relevancy_excellent_count || 0}
                  </div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    良好 (60-80%)
                    <MetricTooltip text="答案相关性得分在60%-80%之间的问题数量，表示答案与问题相关，但仍有改进空间。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_relevancy_good_count || 0}
                  </div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    一般 (40-60%)
                    <MetricTooltip text="答案相关性得分在40%-60%之间的问题数量，表示答案与问题相关性一般，需要改进。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_relevancy_fair_count || 0}
                  </div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    较差 (&lt;40%)
                    <MetricTooltip text="答案相关性得分低于40%的问题数量，表示答案与问题相关性较差，需要重点关注和改进。" />
                  </div>
                  <div className="metric-value">
                    {result.summary.ragas_relevancy_poor_count || 0}
                  </div>
                </div>
              </div>
            </div>
          )}
            
          {/* 按问题类型的相关性得分 - 卡片形式，同一层级 */}
          {result.summary.S1_relevancy_score_percentage !== undefined && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>按问题类型的相关性得分</h3>
                <p className="section-subtitle">各类型问题的相关性表现</p>
              </div>
              <div className="dashboard-metrics">
                {["S1", "S2", "S3", "S4", "S5", "S6"].map((type) => {
                  const score = result.summary[`${type}_relevancy_score_percentage`];
                  const count = result.summary[`${type}_count`];
                  if (score === undefined) return null;
                  
                  const typeNames: Record<string, string> = {
                    "S1": "数值问答",
                    "S2": "定义问答",
                    "S3": "多选题",
                    "S4": "单文件多段",
                    "S5": "多文件多段",
                    "S6": "对抗数据/敏感信息"
                  };
                  
                  return (
                    <div key={type} className="metric-card apple-style">
                      <div className="metric-label">
                        {type}
                        <MetricTooltip text={`${typeNames[type] || type}类型问题的平均相关性得分。得分越高，表示该类型问题的答案质量越好。`} />
                      </div>
                      <div className="metric-value">
                        {score.toFixed(2)}%
                      </div>
                      <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                        {count || 0} 个问题
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 每题明细 - 同一层级 */}
          {perQuestionItems.length > 0 && (
            <div className="dashboard-section" style={{ marginTop: "32px" }}>
              <div className="section-header">
                <h3>每题明细</h3>
                <p className="section-subtitle">查看每个问题的详细评测得分</p>
              </div>
                <div className="csv-preview-controls" style={{ marginBottom: "12px" }}>
                  <label>
                    每页显示：
                    <select
                      value={itemPageSize}
                      onChange={(e) => {
                        setItemPageSize(Number(e.target.value));
                        setItemPage(1);
                      }}
                      className="rows-per-page-select"
                    >
                      <option value={10}>10 行</option>
                      <option value={20}>20 行</option>
                      <option value={50}>50 行</option>
                    </select>
                  </label>
                  <span className="csv-total-rows">
                    共 {perQuestionItems.length} 条
                  </span>
                </div>

                <div className="csv-table-container">
                  <table className="csv-table">
                    <thead>
                      <tr>
                        <th>题目</th>
                        <th>答案</th>
                        <th>类型</th>
                        <th>章节匹配</th>
                        <th>相关性</th>
                        <th>答案质量</th>
                        <th>忠实度</th>
                        <th>混合得分</th>
                      </tr>
                    </thead>
                    <tbody>
                      {perQuestionItems
                        .slice((itemPage - 1) * itemPageSize, itemPage * itemPageSize)
                        .map((item, idx) => {
                          const chapterAcc = item.chapter_match_accuracy ?? (item.chapter_matched ? 1 : null);
                          const relevancy = item.ragas_relevancy_score;
                          const quality = item.ragas_quality_score;
                          const faithfulness = item.ragas_faithfulness_score;
                          const hybrid = item.hybrid_score;
                          return (
                            <tr key={`${itemPage}-${idx}`}>
                              <td className="cell-question">{item.question}</td>
                              <td className="cell-answer">{item.answer}</td>
                              <td>{item.type || "-"}</td>
                              <td>{chapterAcc !== undefined && chapterAcc !== null ? formatScore(chapterAcc) : "-"}</td>
                              <td>{formatScore(relevancy)}</td>
                              <td>{formatScore(quality)}</td>
                              <td>{formatScore(faithfulness)}</td>
                              <td>{formatScore(hybrid)}</td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>

                {perQuestionItems.length > itemPageSize && (
                  <div className="csv-pagination">
                    <button
                      onClick={() => setItemPage((p) => Math.max(1, p - 1))}
                      disabled={itemPage === 1}
                      className="page-btn"
                    >
                      上一页
                    </button>
                    <span className="page-info">
                      第 {itemPage} / {Math.ceil(perQuestionItems.length / itemPageSize)} 页
                    </span>
                    <button
                      onClick={() =>
                        setItemPage((p) =>
                          Math.min(Math.ceil(perQuestionItems.length / itemPageSize), p + 1)
                        )
                      }
                      disabled={itemPage >= Math.ceil(perQuestionItems.length / itemPageSize)}
                      className="page-btn"
                    >
                      下一页
                    </button>
                  </div>
                )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default Evaluation;
