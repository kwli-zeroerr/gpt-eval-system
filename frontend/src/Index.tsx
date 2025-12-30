import { useState, useEffect, useRef } from "react";
import QuestionGen from "./modules/QuestionGen";
import QuestionEdit from "./modules/QuestionEdit";
import FormatConvert from "./modules/FormatConvert";
import Retrieval from "./modules/Retrieval";
import Evaluation from "./modules/Evaluation";

// Tooltip 组件（与评测页面保持一致）
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

type ModuleStatus = "pending" | "in_progress" | "completed";

interface ModuleInfo {
  id: string;
  name: string;
  status: ModuleStatus;
  component: React.ComponentType;
}

const MODULES: ModuleInfo[] = [
  { id: "question_gen", name: "问题生成", status: "completed", component: QuestionGen },
  { id: "question_edit", name: "问题编辑", status: "completed", component: QuestionEdit },
  { id: "format_convert", name: "格式转换", status: "completed", component: FormatConvert },
  { id: "retrieval", name: "检索", status: "pending", component: Retrieval },
  { id: "evaluation", name: "评测", status: "pending", component: Evaluation },
];

// 定义管道模块（排除问题编辑，因为它是独立模块，不在管道流程中）
const PIPELINE_MODULES = MODULES.filter(m => 
  m.id !== "question_edit" && 
  ["question_gen", "format_convert", "retrieval", "evaluation"].includes(m.id)
);

function Index() {
  const [activeModule, setActiveModule] = useState<string>("overview");
  const [pipelineStep, setPipelineStep] = useState<number>(0); // Pipeline progress (only updated by pipeline)
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineProgress, setPipelineProgress] = useState<Record<string, string>>({});
  const [pipelineResults, setPipelineResults] = useState<any>(null);
  const [latestSummary, setLatestSummary] = useState<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [perCategoryQuestions, setPerCategoryQuestions] = useState<number>(5); // 每类问题数量
  const [questionGenProgress, setQuestionGenProgress] = useState<{
    currentCategory: string;
    completedCategories: string[];
    totalCategories: number;
  } | null>(null);
  const [retrievalProgress, setRetrievalProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [sourceDocuments, setSourceDocuments] = useState<any>(null);
  const [questionAnalysis, setQuestionAnalysis] = useState<any>(null);

  const activeModuleInfo = MODULES.find((m) => m.id === activeModule);

  // Show overview page when no specific module is selected, or show module details
  const showOverview = activeModule === "overview";

  // 已完成的模块数量（0 ~ PIPELINE_MODULES.length）
  // 注意：使用 PIPELINE_MODULES 而不是 MODULES，因为 question_edit 不在管道流程中
  const completedSteps = Math.min(Math.max(pipelineStep, 0), PIPELINE_MODULES.length);

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  // 根据一键运行状态更新最新评测概要（不再定时轮询）
  const fetchLatestSummary = async () => {
    try {
      const response = await fetch("/api/evaluation/latest-summary");
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setLatestSummary(data);
    } catch (e) {
      // 静默处理错误，避免在控制台显示过多错误信息
      if (process.env.NODE_ENV === 'development') {
        console.error("Failed to fetch summary:", e);
      }
    }
  };

  // 获取源文档信息
  const fetchSourceDocuments = async () => {
    try {
      const response = await fetch("/api/source-documents");
      if (response.ok) {
        const data = await response.json();
        setSourceDocuments(data);
      }
    } catch (e) {
      if (process.env.NODE_ENV === 'development') {
        console.error("Failed to fetch source documents:", e);
      }
    }
  };

  // 获取问题分析
  const fetchQuestionAnalysis = async () => {
    try {
      const response = await fetch("/api/question-analysis");
      if (response.ok) {
        const data = await response.json();
        setQuestionAnalysis(data);
      }
    } catch (e) {
      if (process.env.NODE_ENV === 'development') {
        console.error("Failed to fetch question analysis:", e);
      }
    }
  };

  // 在组件挂载时或切换到概览页面时获取源文档信息
  useEffect(() => {
    if (showOverview) {
      fetchSourceDocuments();
      fetchQuestionAnalysis();
    }
  }, [showOverview]);

  const handleRunPipeline = () => {
    if (pipelineRunning) return;
    
    setPipelineRunning(true);
    setPipelineProgress({});
    setPipelineResults(null);
    // 重置上一次运行遗留的进度，避免一键运行时直接显示“已检索 10/10”等旧状态
    setQuestionGenProgress(null);
    setRetrievalProgress(null);
    
    // Connect WebSocket
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/pipeline`);
    wsRef.current = ws;
    
    ws.onopen = () => {
      // Send pipeline request with user-selected settings
      ws.send(JSON.stringify({
        categories: ["S1", "S2", "S3", "S4", "S5", "S6"],
        per_category: perCategoryQuestions,
        prompt_overrides: {},
        source_files: [],
      }));
      // Initialize question generation progress
      setQuestionGenProgress({
        currentCategory: "",
        completedCategories: [],
        totalCategories: 6,
      });
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === "module_progress") {
        const { module, status, data: moduleData } = data;
        // #region agent log
        fetch('http://localhost:7242/ingest/3cf65726-16c2-439c-9bb4-4385b0187030',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'Index.tsx:136',message:'Received module_progress',data:{module,status,moduleData},timestamp:Date.now(),sessionId:'debug-session',runId:'pipeline-frontend',hypothesisId:'C'})}).catch(()=>{});
        // #endregion
        
        // Handle question generation category progress first
        if (module === "question_gen") {
          if (status === "complete") {
            // Question generation fully completed
            setPipelineProgress((prev) => ({
              ...prev,
              [module]: "complete",
            }));
            // Clear current category when fully complete
            setQuestionGenProgress((prev) => {
              if (!prev) return null;
              return {
                ...prev,
                currentCategory: "",
              };
            });
            // Update step only when fully complete
            const moduleIndex = MODULES.findIndex((m) => m.id === module);
            if (moduleIndex >= 0) {
              setPipelineStep(moduleIndex + 1);
            }
          } else if (moduleData) {
            if (moduleData.category) {
              // Category is being processed
              setQuestionGenProgress((prev) => {
                if (!prev) {
                  // Initialize if not exists
                  return {
                    currentCategory: moduleData.category,
                    completedCategories: [],
                    totalCategories: 6,
                  };
                }
                return {
                  ...prev,
                  currentCategory: moduleData.category,
                };
              });
              // Update status to "progress" to show it's actively running
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "progress",
              }));
            }
            if (moduleData.category_complete) {
              // Category completed
              setQuestionGenProgress((prev) => {
                if (!prev) {
                  return {
                    currentCategory: "",
                    completedCategories: [moduleData.category_complete],
                    totalCategories: 6,
                  };
                }
                const completed = [...prev.completedCategories];
                if (!completed.includes(moduleData.category_complete)) {
                  completed.push(moduleData.category_complete);
                }
                return {
                  ...prev,
                  completedCategories: completed,
                  currentCategory: "",
                };
              });
              // Keep status as "progress" while categories are still being generated
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "progress",
              }));
            }
            if (status === "start") {
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "progress",
              }));
              const moduleIndex = MODULES.findIndex((m) => m.id === module);
              if (moduleIndex >= 0) {
                setPipelineStep(moduleIndex);
              }
            }
          }
        } else {
          // 优先处理 complete 状态，确保状态立即更新
          if (status === "complete") {
            const moduleIndex = MODULES.findIndex((m) => m.id === module);
            if (moduleIndex >= 0) {
              // 立即更新状态为完成
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "complete",
              }));
              // 推进到下一步
              setPipelineStep(moduleIndex + 1);
              // 清除检索进度（如果检索完成）
              if (module === "retrieval") {
                setRetrievalProgress(null);
              }
            }
          } else if (status === "start") {
            // 模块开始，设置当前步骤
            const moduleIndex = MODULES.findIndex((m) => m.id === module);
            if (moduleIndex >= 0) {
              // 立即更新步骤和状态，确保UI正确显示
              setPipelineStep(moduleIndex);
              setPipelineProgress((prev) => {
                const newProgress = {
                  ...prev,
                  [module]: "progress",
                };
                // 确保前一个模块已完成（如果存在）
                if (moduleIndex > 0) {
                  const prevModule = MODULES[moduleIndex - 1];
                  if (prevModule && prev[prevModule.id] !== "complete" && prev[prevModule.id] !== "skipped") {
                    newProgress[prevModule.id] = "complete";
                  }
                }
                return newProgress;
              });
            }
          } else if (status === "skipped") {
            // 模块跳过，前进到下一步
            const moduleIndex = MODULES.findIndex((m) => m.id === module);
            if (moduleIndex >= 0) {
              setPipelineStep(moduleIndex + 1);
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "skipped",
              }));
            }
          } else {
            // 处理检索阶段的进度更新
            if (module === "retrieval" && moduleData) {
              if (typeof moduleData.current === "number" && typeof moduleData.total === "number") {
                setRetrievalProgress({
                  current: moduleData.current,
                  total: moduleData.total,
                });

                // 如果检索进度已达总数，但未收到 complete 状态，也视为完成，推进到下一阶段
                if (moduleData.total > 0 && moduleData.current >= moduleData.total) {
                  setPipelineProgress((prev) => ({
                    ...prev,
                    [module]: "complete",
                  }));
                  const moduleIndex = MODULES.findIndex((m) => m.id === module);
                  if (moduleIndex >= 0) {
                    setPipelineStep(moduleIndex + 1);
                  }
                  setRetrievalProgress(null);
                }
              }
            }
            
            // 对于 progress 状态，更新进度但不改变步骤
            if (status === "progress") {
              setPipelineProgress((prev) => ({
                ...prev,
                [module]: "progress",
              }));
            }
          }
        }
      } else if (data.type === "complete") {
        setPipelineResults(data.results);
        setPipelineRunning(false);
        
        // 确保所有模块的状态都正确更新
        const finalProgress: Record<string, string> = {};
        if (data.results?.question_gen) {
          finalProgress["question_gen"] = "complete";
        }
        if (data.results?.format_convert) {
          finalProgress["format_convert"] = "complete";
        }
        if (data.results?.retrieval) {
          if (data.results.retrieval.status === "skipped") {
            finalProgress["retrieval"] = "skipped";
          } else {
            finalProgress["retrieval"] = "complete";
          }
        }
        if (data.results?.evaluation) {
          if (data.results.evaluation.status === "skipped") {
            finalProgress["evaluation"] = "skipped";
          } else {
            finalProgress["evaluation"] = "complete";
          }
        }
        setPipelineProgress(finalProgress);
        
        // 设置最终步骤为所有模块完成
        setPipelineStep(PIPELINE_MODULES.length);
        
        // 清除进度数据
        setRetrievalProgress(null);
        
        // 一键运行完成后刷新一次最新评测结果
        fetchLatestSummary();
        
        // 触发自动加载最新文件的事件（通知各模块刷新文件列表）
        if (data.results?.question_gen?.log_path) {
          window.dispatchEvent(new CustomEvent('pipeline-complete-question-gen', {
            detail: { log_path: data.results.question_gen.log_path }
          }));
        }
        if (data.results?.format_convert?.log_path) {
          window.dispatchEvent(new CustomEvent('pipeline-complete-format-convert', {
            detail: { log_path: data.results.format_convert.log_path }
          }));
        }
        if (data.results?.retrieval?.output_csv_path) {
          window.dispatchEvent(new CustomEvent('pipeline-complete-retrieval', {
            detail: { csv_path: data.results.retrieval.output_csv_path }
          }));
        }
        if (data.results?.evaluation?.summary_json_path) {
          window.dispatchEvent(new CustomEvent('pipeline-complete-evaluation', {
            detail: { summary_path: data.results.evaluation.summary_json_path }
          }));
        }
        
        ws.close();
      } else if (data.type === "error") {
        setPipelineProgress((prev) => ({
          ...prev,
          error: data.message,
        }));
        setPipelineRunning(false);
        ws.close();
      }
    };
    
    ws.onerror = (error) => {
      // 静默处理 WebSocket 错误，避免在控制台显示过多错误信息
      if (process.env.NODE_ENV === 'development') {
        console.error("WebSocket error:", error);
      }
      setPipelineProgress((prev) => ({
        ...prev,
        error: "连接错误",
      }));
      setPipelineRunning(false);
    };
    
    ws.onclose = () => {
      wsRef.current = null;
    };
  };

  return (
    <div className="index-page">
      {/* Header with module tabs */}
      <header className="module-header">
        <div className="module-tabs">
          <button
            className={`module-tab ${showOverview ? "active" : ""}`}
            onClick={() => setActiveModule("overview")}
          >
            <span className="module-name">概览</span>
          </button>
          {MODULES.map((module, idx) => (
            <button
              key={module.id}
              className={`module-tab ${activeModule === module.id ? "active" : ""}`}
              onClick={() => {
                setActiveModule(module.id);
              }}
            >
              <span className="module-number">{idx + 1}</span>
              <span className="module-name">{module.name}</span>
            </button>
          ))}
        </div>
      </header>

      {/* Content Area */}
      {showOverview ? (
        /* Overview Page - Status and Quick Actions */
        <div className="overview-page">
          {/* Pipeline Status Card */}
          <div className="overview-status" id="overview-status">
            {/* Circular Progress Indicator (Example 8 style) */}
            <div className="circular-progress-section">
              <div className="circular-progress-container">
                <svg className="circular-progress" viewBox="0 0 100 100">
                  <circle
                    className="progress-bg"
                    cx="50"
                    cy="50"
                    r="45"
                    fill="none"
                    stroke="#e5e7eb"
                    strokeWidth="8"
                  />
                  <circle
                    className="progress-fill-circle"
                    cx="50"
                    cy="50"
                    r="45"
                    fill="none"
                    stroke="#4f46e5"
                    strokeWidth="8"
                    strokeLinecap="round"
                    strokeDasharray={`${2 * Math.PI * 45}`}
                    strokeDashoffset={`${2 * Math.PI * 45 * (1 - (completedSteps || 0) / PIPELINE_MODULES.length)}`}
                    transform="rotate(-90 50 50)"
                  />
                </svg>
                <div className="circular-progress-text">
                  <div className="progress-ratio">
                    {completedSteps} of {PIPELINE_MODULES.length}
                  </div>
                </div>
              </div>
              <div className="circular-progress-info">
                <div className="current-step-name">
                  {pipelineStep < PIPELINE_MODULES.length ? PIPELINE_MODULES[pipelineStep]?.name : "全部完成"}
                </div>
                <div className="current-step-desc">
                  {pipelineStep < PIPELINE_MODULES.length 
                    ? (pipelineRunning ? "正在处理中..." : "等待运行")
                    : "所有模块已完成"}
                </div>
              </div>
            </div>

            {/* Segmented Progress Bar (Example 7 style) */}
            <div className="segmented-progress-container">
              <div className="segmented-progress-bar">
                {PIPELINE_MODULES.map((module, idx) => (
                  <div
                    key={module.id}
                    className={`progress-segment ${idx <= pipelineStep ? "completed" : ""} ${idx === pipelineStep ? "active" : ""}`}
                  >
                    <div className="segment-content">
                      {module.name}
                    </div>
                    {idx < PIPELINE_MODULES.length - 1 && (
                      <div className={`segment-arrow ${idx < pipelineStep ? "completed" : ""}`}></div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Quick Actions Card */}
          <div className="quick-actions">
            <div className="section-header">
            <h3>快速操作</h3>
              <p className="section-subtitle">配置并运行完整的评测流程</p>
            </div>
            <div className="pipeline-controls">
              <div className="pipeline-config-inline">
                <label htmlFor="per-category-questions" className="config-label">
                  <span className="config-label-text">每类问题数量</span>
                  <div className="config-input-wrapper">
                    <input
                      id="per-category-questions"
                      type="number"
                      min={1}
                      max={20}
                      value={perCategoryQuestions}
                      onChange={(e) => setPerCategoryQuestions(Number(e.target.value))}
                      disabled={pipelineRunning}
                      className="config-input-inline"
                    />
                    <span className="config-unit">个/类</span>
                  </div>
                </label>
                <div className="config-summary">
                  共 <strong>{perCategoryQuestions * 6}</strong> 个问题
                </div>
              </div>
              <button
                className={`action-btn primary ${pipelineRunning ? "running" : ""}`}
                onClick={handleRunPipeline}
                disabled={pipelineRunning}
              >
                <span className="action-icon">{pipelineRunning ? "⟳" : "🚀"}</span>
                <span className="action-text">
                  {pipelineRunning ? "运行中..." : "一键运行全部流程"}
                </span>
              </button>
            </div>
            
            {/* Pipeline Progress - Combined with Question Generation Progress */}
            {pipelineRunning && (
              <div className="pipeline-progress">
                <div className="progress-title">运行进度</div>
                <div className="progress-modules">
                  {PIPELINE_MODULES.map((module, idx) => {
                    const moduleKey = module.id;
                    // 优先使用 pipelineStep 来判断状态，确保与 segmented-progress-container 同步
                    // 如果 pipelineStep 已经超过当前模块，说明已完成
                    // 如果 pipelineStep 等于当前模块索引，说明正在进行
                    // 如果 pipelineStep 小于当前模块索引，说明等待中
                    let status: string;
                    if (idx < pipelineStep) {
                      // 已完成
                      status = "complete";
                    } else if (idx === pipelineStep) {
                      // 正在进行，使用 pipelineProgress 中的详细状态
                      status = pipelineProgress[moduleKey] || "progress";
                    } else {
                      // 等待中
                      status = "pending";
                    }
                    
                    const isQuestionGen = module.id === "question_gen";
                    // Show category progress when question-gen is active or has progress data
                    // Keep showing until all 6 categories are completed or module moves to next step
                    const hasProgressData = questionGenProgress && (questionGenProgress.completedCategories.length > 0 || questionGenProgress.currentCategory);
                    const showCategoryProgress = isQuestionGen && hasProgressData && (status === "start" || status === "progress" || status === "complete");
                    
                    return (
                      <div key={module.id} className={`progress-module ${status}`}>
                        <span className="module-indicator">
                          {status === "complete" ? "✓" : status === "start" || status === "progress" ? "⟳" : idx + 1}
                        </span>
                        <span className="module-name">{module.name}</span>
                        <span className="module-status">
                          {status === "complete" ? "完成" : status === "start" || status === "progress" ? "进行中" : "等待中"}
                        </span>
                        
                        {/* S1-S6 Category Progress - Integrated into question-gen module */}
                        {showCategoryProgress && (
                          <div className="qg-categories-bar-inline">
                            {["S1", "S2", "S3", "S4", "S5", "S6"].map((cat) => {
                              const isCompleted = questionGenProgress.completedCategories.includes(cat);
                              const isCurrent = questionGenProgress.currentCategory === cat;
                              return (
                                <div
                                  key={cat}
                                  className={`qg-category-item-inline ${isCompleted ? "completed" : ""} ${isCurrent ? "active" : ""}`}
                                  title={isCompleted ? `${cat} 已完成` : isCurrent ? `正在生成 ${cat}` : `${cat} 等待中`}
                                >
                                  {isCompleted ? "✓" : isCurrent ? "⟳" : cat}
                                </div>
                              );
                            })}
                          </div>
                        )}
                        
                        {showCategoryProgress && questionGenProgress.currentCategory && (
                          <div className="qg-current-status-inline">
                            正在生成 {questionGenProgress.currentCategory} 类别问题...
                          </div>
                        )}

                        {/* 检索阶段的总体进度条 */}
                        {module.id === "retrieval" && retrievalProgress && (
                          <div className="retrieval-progress-inline">
                            <div className="retrieval-progress-label">
                              <span className="retrieval-progress-number">{retrievalProgress.current}</span>
                              <span className="retrieval-progress-separator">/</span>
                              <span className="retrieval-progress-total">{retrievalProgress.total}</span>
                              <span className="retrieval-progress-unit">个问题</span>
                            </div>
                            <div className="retrieval-progress-bar">
                              <div
                                className="retrieval-progress-fill"
                                style={{
                                  width:
                                    retrievalProgress.total > 0
                                      ? `${Math.min(
                                          100,
                                          Math.round(
                                            (retrievalProgress.current / retrievalProgress.total) * 100
                                          )
                                        )}%`
                                      : "0%",
                                }}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            
            {/* Pipeline Results */}
            {pipelineResults && !pipelineRunning && (
              <div className="pipeline-results">
                <div className="results-title">运行结果</div>
                <div className="results-summary">
                  {pipelineResults.question_gen && (
                    <div className="result-item">
                      <strong>问题生成：</strong>
                      生成了 {pipelineResults.question_gen.total_questions} 个问题
                      （耗时 {pipelineResults.question_gen.total_time?.toFixed(2)}s）
                    </div>
                  )}
                  {pipelineResults.format_convert && (
                    <div className="result-item">
                      <strong>格式转换：</strong>
                      CSV 文件已生成
                    </div>
                  )}
                  {pipelineResults.retrieval && (
                    <div className={`result-item ${pipelineResults.retrieval.status === "skipped" ? "skipped" : ""}`}>
                      <strong>检索：</strong>
                      {pipelineResults.retrieval.status === "skipped"
                        ? pipelineResults.retrieval.message || "未执行检索"
                        : `完成 ${pipelineResults.retrieval.completed}/${pipelineResults.retrieval.total_questions} 个问题（耗时 ${pipelineResults.retrieval.total_time?.toFixed?.(2) ?? "0.00"}s）`}
                    </div>
                  )}
                  {pipelineResults.evaluation && (
                    <div className={`result-item ${pipelineResults.evaluation.status === "skipped" ? "skipped" : ""}`}>
                      <strong>评测：</strong>
                      {pipelineResults.evaluation.status === "skipped"
                        ? pipelineResults.evaluation.message || "未执行评测"
                        : (() => {
                            const summary = pipelineResults.evaluation.summary || {};
                            const parts = [];
                            
                            // 优先显示相关性得分（主要指标，也是用户满意度指标）
                            if (summary.ragas_relevancy_score_percentage !== undefined) {
                              parts.push(`相关性 ${summary.ragas_relevancy_score_percentage.toFixed(2)}%`);
                            }
                            // 显示章节匹配准确率（辅助指标）
                            if (summary.chapter_match_accuracy_percentage !== undefined || summary.accuracy_percentage !== undefined) {
                              parts.push(`准确率 ${((summary.chapter_match_accuracy_percentage ?? summary.accuracy_percentage ?? 0).toFixed(2))}%`);
                            }
                            
                            const metricsText = parts.length > 0 ? parts.join("，") : "已完成";
                            return `${metricsText}（耗时 ${pipelineResults.evaluation.total_time?.toFixed?.(2) ?? "0.00"}s）`;
                          })()}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Source Documents Information */}
          {sourceDocuments && (
            <div className="dashboard-section" style={{ marginBottom: "24px" }}>
              <div className="section-header">
                <h3>源文档信息</h3>
                <p className="section-subtitle">
                  知识库文档统计 - 用于生成问题的源文档信息
                  <MetricTooltip text="这些是存储在MinIO知识库中的源文档，系统会从这些文档中提取内容生成问题。文档数量越多，生成的问题越多样化。" />
                </p>
              </div>
              <div className="dashboard-metrics">
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    总文档数
                    <MetricTooltip text="知识库中可用于生成问题的文档总数" />
                  </div>
                  <div className="metric-value">{sourceDocuments.total_files || 0}</div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    数据集数量
                    <MetricTooltip text="不同的数据集（通常对应不同的文档集合）数量" />
                  </div>
                  <div className="metric-value">{sourceDocuments.total_datasets || 0}</div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    平均文档/数据集
                    <MetricTooltip text="每个数据集平均包含的文档数量" />
                  </div>
                  <div className="metric-value">{sourceDocuments.avg_files_per_dataset?.toFixed(1) || "0.0"}</div>
                </div>
                {sourceDocuments.statistics?.most_common_type && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      主要文件类型
                      <MetricTooltip text="知识库中最常见的文件扩展名" />
                    </div>
                    <div className="metric-value">.{sourceDocuments.statistics.most_common_type}</div>
                  </div>
                )}
              </div>
              {sourceDocuments.datasets && sourceDocuments.datasets.length > 0 && (
                <div style={{ marginTop: "20px", padding: "16px", background: "#f9fafb", borderRadius: "8px", fontSize: "14px", color: "#6b7280" }}>
                  <strong style={{ color: "#374151" }}>数据集列表：</strong>
                  <div style={{ marginTop: "8px", display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {sourceDocuments.datasets.slice(0, 10).map((ds: any) => (
                      <span key={ds.dataset_id} style={{ 
                        padding: "4px 8px", 
                        background: "#fff", 
                        borderRadius: "4px",
                        border: "1px solid #e5e7eb"
                      }}>
                        {ds.dataset_id} <span style={{ color: "#9ca3af" }}>({ds.file_count})</span>
                      </span>
                    ))}
                    {sourceDocuments.datasets.length > 10 && (
                      <span style={{ padding: "4px 8px", color: "#9ca3af" }}>
                        ... 还有 {sourceDocuments.datasets.length - 10} 个数据集
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Question Analysis */}
          {questionAnalysis && !questionAnalysis.error && (
            <div className="dashboard-section">
              <div className="section-header">
                <h3>问题泛化性分析</h3>
                <p className="section-subtitle">
                  已生成问题的类型分布
                  <MetricTooltip text="问题泛化性分析用于评估生成的问题是否足够泛化，以测试GPT系统在面对抽象、跨文档、流程类问题时的应对能力，而不仅仅是具体数值或错误码的查找。" />
                </p>
              </div>
              <div className="dashboard-metrics">
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    具体问题
                    <MetricTooltip text="包含具体数值、错误码、章节引用等的问题。例如：'I/O转接模块的输入电源电压是多少？'、'报错信息0x7314是什么？'、'第4章第23页的内容是什么？'" />
                  </div>
                  <div className="metric-value">{questionAnalysis.ratios?.specific?.toFixed(1) || "0.0"}%</div>
                  <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                    {questionAnalysis.specific_questions || 0} 个
                  </div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    泛化问题
                    <MetricTooltip text="包含抽象概念、通用流程、跨文档的问题。例如：'如何配置系统？'（方法类）、'为什么会出现这个错误？'（原因类）、'什么是负载端编码器？'（概念类）、'系统配置流程是什么？'（流程类）" />
                  </div>
                  <div className="metric-value">{questionAnalysis.ratios?.generalization?.toFixed(1) || "0.0"}%</div>
                  <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                    {questionAnalysis.generalization_questions || 0} 个
                  </div>
                </div>
                <div className="metric-card apple-style">
                  <div className="metric-label">
                    混合问题
                    <MetricTooltip text="同时包含具体和泛化特征的问题。例如：'如何解决0x7314错误？'（既有错误码，又有方法）" />
                  </div>
                  <div className="metric-value">{questionAnalysis.ratios?.mixed?.toFixed(1) || "0.0"}%</div>
                  <div style={{ fontSize: "12px", opacity: 0.7, marginTop: "4px", fontWeight: 400 }}>
                    {questionAnalysis.mixed_questions || 0} 个
                  </div>
                </div>
                <div className="metric-card apple-style" style={{
                  border: questionAnalysis.generalization_level === "high" ? "2px solid #10b981" : 
                          questionAnalysis.generalization_level === "medium" ? "2px solid #f59e0b" : 
                          "1px solid #e5e7eb"
                }}>
                  <div className="metric-label">
                    泛化级别
                    <MetricTooltip text="根据三类问题的比例计算的整体泛化级别：高泛化（泛化问题占比>50%）、中等泛化（混合问题占比>40%）、低泛化（具体问题占比>60%）、平衡（其他情况）。泛化级别越高，越能测试GPT系统应对抽象问题的能力。" />
                  </div>
                  <div className="metric-value" style={{
                    color: questionAnalysis.generalization_level === "high" ? "#10b981" : 
                           questionAnalysis.generalization_level === "medium" ? "#f59e0b" : "#6b7280"
                  }}>
                    {questionAnalysis.generalization_level === "high" ? "高" : 
                     questionAnalysis.generalization_level === "medium" ? "中" : 
                     questionAnalysis.generalization_level === "low" ? "低" : "平衡"}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Dashboard with Core Metrics */}
          {latestSummary && latestSummary.summary && (
            <div className="dashboard-section">
              <div className="section-header">
                <h3>核心指标</h3>
                <p className="section-subtitle">最新评测结果概览</p>
              </div>
              <div className="dashboard-metrics">
                {/* 答案相关性 - 主要指标 */}
                {latestSummary.summary.ragas_relevancy_score_percentage !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      答案相关性
                      <MetricTooltip text="评估答案与问题的相关程度，反映系统回答是否直接、准确地解决了用户问题。该指标同时作为用户满意度的衡量标准。" />
                    </div>
                    <div className="metric-value">
                      {latestSummary.summary.ragas_relevancy_score_percentage?.toFixed(2) || "0.00"}%
                    </div>
                  </div>
                )}
                {/* 答案质量 */}
                {latestSummary.summary.ragas_quality_score_percentage !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      答案质量
                      <MetricTooltip text="评估答案的准确性、完整性和一致性，反映答案的整体质量水平。" />
                    </div>
                    <div className="metric-value">
                      {latestSummary.summary.ragas_quality_score_percentage?.toFixed(2) || "0.00"}%
                    </div>
                  </div>
                )}
                {/* 章节匹配准确率 - 辅助指标 */}
                {(latestSummary.summary.chapter_match_accuracy_percentage !== undefined || latestSummary.summary.accuracy_percentage !== undefined) && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      章节匹配准确率
                      <MetricTooltip text="基于章节信息匹配的传统准确率指标，反映答案与参考章节的匹配程度。正确匹配的答案数占总问题数的比例。" />
                    </div>
                    <div className="metric-value">
                      {(latestSummary.summary.chapter_match_accuracy_percentage ?? latestSummary.summary.accuracy_percentage ?? 0).toFixed(2)}%
                    </div>
                  </div>
                )}
                {/* 总问题数 */}
                <div className="metric-card apple-style">
                    <div className="metric-label">
                      总问题数
                      <MetricTooltip text="本次评测包含的问题总数。" />
                    </div>
                  <div className="metric-value">
                    {latestSummary.summary.total_questions || 0}
                  </div>
                </div>
                {/* 检索成功率 */}
                {latestSummary.summary.retrieval_success_rate_percentage !== undefined && (
                  <div className="metric-card apple-style">
                    <div className="metric-label">
                      检索成功率
                      <MetricTooltip text="成功检索到答案的问题占总问题的比例，反映检索系统的可用性和稳定性。" />
                    </div>
                    <div className="metric-value">
                      {latestSummary.summary.retrieval_success_rate_percentage?.toFixed(2) || "0.00"}%
                    </div>
                  </div>
                )}
              </div>
              
              {/* 相关性得分分布 */}
              {latestSummary.summary.ragas_relevancy_excellent_count !== undefined && (
                <div className="dashboard-section" style={{ marginTop: "24px" }}>
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
                        {latestSummary.summary.ragas_relevancy_excellent_count || 0}
                      </div>
                    </div>
                    <div className="metric-card apple-style">
                      <div className="metric-label">
                        良好 (60-80%)
                        <MetricTooltip text="答案相关性得分在60%-80%之间的问题数量，表示答案与问题相关，但仍有改进空间。" />
                      </div>
                      <div className="metric-value">
                        {latestSummary.summary.ragas_relevancy_good_count || 0}
                      </div>
                    </div>
                    <div className="metric-card apple-style">
                      <div className="metric-label">
                        一般 (40-60%)
                        <MetricTooltip text="答案相关性得分在40%-60%之间的问题数量，表示答案与问题相关性一般，需要改进。" />
                      </div>
                      <div className="metric-value">
                        {latestSummary.summary.ragas_relevancy_fair_count || latestSummary.summary.ragas_relevancy_average_count || 0}
                      </div>
                    </div>
                    <div className="metric-card apple-style">
                      <div className="metric-label">
                        较差 (&lt;40%)
                        <MetricTooltip text="答案相关性得分低于40%的问题数量，表示答案与问题相关性较差，需要重点关注和改进。" />
                      </div>
                      <div className="metric-value">
                        {latestSummary.summary.ragas_relevancy_poor_count || 0}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              {/* 按问题类型的相关性分布 */}
              {latestSummary.summary.S1_relevancy_score_percentage !== undefined && (
                <div className="dashboard-section" style={{ marginTop: "24px" }}>
                  <div className="section-header">
                    <h3>按问题类型的相关性得分</h3>
                    <p className="section-subtitle">各类型问题的相关性表现</p>
                  </div>
                  <div className="dashboard-metrics">
                    {["S1", "S2", "S3", "S4", "S5", "S6"].map((type) => {
                      const score = latestSummary.summary[`${type}_relevancy_score_percentage`];
                      const count = latestSummary.summary[`${type}_count`];
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
                            <span className="metric-info-icon" title={`${typeNames[type] || type}类型问题的平均相关性得分。得分越高，表示该类型问题的答案质量越好。`}>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <circle cx="12" cy="12" r="10"/>
                                <line x1="12" y1="16" x2="12" y2="12"/>
                                <line x1="12" y1="8" x2="12.01" y2="8"/>
                              </svg>
                            </span>
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
            </div>
          )}
        </div>
      ) : (
        /* Module Detail Page */
        <div className="module-content">
          {MODULES.map((module) => {
            const ModuleComponent = module.component;
            return (
              <div
                key={module.id}
                style={{ display: activeModule === module.id ? "block" : "none" }}
              >
                <ModuleComponent />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default Index;

