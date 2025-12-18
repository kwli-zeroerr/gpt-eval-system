import { useState, useEffect, useRef } from "react";
import QuestionGen from "./modules/QuestionGen";
import FormatConvert from "./modules/FormatConvert";
import Retrieval from "./modules/Retrieval";
import Evaluation from "./modules/Evaluation";

type ModuleStatus = "pending" | "in_progress" | "completed";

interface ModuleInfo {
  id: string;
  name: string;
  status: ModuleStatus;
  component: React.ComponentType;
}

const MODULES: ModuleInfo[] = [
  { id: "question-gen", name: "问题生成", status: "completed", component: QuestionGen },
  { id: "format-convert", name: "格式转换", status: "completed", component: FormatConvert },
  { id: "retrieval", name: "检索", status: "pending", component: Retrieval },
  { id: "evaluation", name: "评测", status: "pending", component: Evaluation },
];

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

  const activeModuleInfo = MODULES.find((m) => m.id === activeModule);

  // Show overview page when no specific module is selected, or show module details
  const showOverview = activeModule === "overview";

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  // Fetch latest evaluation summary for dashboard
  useEffect(() => {
    const fetchSummary = async () => {
      try {
        const response = await fetch("/api/evaluation/latest-summary");
        const data = await response.json();
        setLatestSummary(data);
      } catch (e) {
        console.error("Failed to fetch summary:", e);
      }
    };
    fetchSummary();
    // Refresh every 30 seconds
    const interval = setInterval(fetchSummary, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleRunPipeline = () => {
    if (pipelineRunning) return;
    
    setPipelineRunning(true);
    setPipelineProgress({});
    setPipelineResults(null);
    
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
        
        // Handle question generation category progress first
        if (module === "question_gen" && moduleData) {
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
        } else {
          // For other modules, update status normally
          setPipelineProgress((prev) => ({
            ...prev,
            [module]: status,
          }));
        }
        
        // Update current step based on module
        const moduleIndex = MODULES.findIndex((m) => {
          if (module === "question_gen") return m.id === "question-gen";
          if (module === "format_convert") return m.id === "format-convert";
          if (module === "retrieval") return m.id === "retrieval";
          if (module === "evaluation") return m.id === "evaluation";
          return false;
        });
        
        if (moduleIndex >= 0) {
          if (status === "start") {
            setPipelineStep(moduleIndex);
          } else if (status === "complete") {
            setPipelineStep(moduleIndex + 1);
          }
        }
      } else if (data.type === "complete") {
        setPipelineResults(data.results);
        setPipelineRunning(false);
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
      console.error("WebSocket error:", error);
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
                    strokeDashoffset={`${2 * Math.PI * 45 * (1 - (pipelineStep + 1) / MODULES.length)}`}
                    transform="rotate(-90 50 50)"
                  />
                </svg>
                <div className="circular-progress-text">
                  <div className="progress-ratio">{pipelineStep + 1} of {MODULES.length}</div>
                </div>
              </div>
              <div className="circular-progress-info">
                <div className="current-step-name">
                  {pipelineStep < MODULES.length ? MODULES[pipelineStep]?.name : "全部完成"}
                </div>
                <div className="current-step-desc">
                  {pipelineStep < MODULES.length 
                    ? (pipelineRunning ? "正在处理中..." : "等待运行")
                    : "所有模块已完成"}
                </div>
              </div>
            </div>

            {/* Segmented Progress Bar (Example 7 style) */}
            <div className="segmented-progress-container">
              <div className="segmented-progress-bar">
                {MODULES.map((module, idx) => (
                  <div
                    key={module.id}
                    className={`progress-segment ${idx <= pipelineStep ? "completed" : ""} ${idx === pipelineStep ? "active" : ""}`}
                  >
                    <div className="segment-content">
                      {module.name}
                    </div>
                    {idx < MODULES.length - 1 && (
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
                  {MODULES.map((module, idx) => {
                    const moduleKey = module.id.replace("-", "_");
                    const status = pipelineProgress[moduleKey] || "pending";
                    const isQuestionGen = module.id === "question-gen";
                    // Show category progress when question-gen is active or has progress data
                    // Keep showing until all 6 categories are completed or module moves to next step
                    const hasProgressData = questionGenProgress && (questionGenProgress.completedCategories.length > 0 || questionGenProgress.currentCategory);
                    const showCategoryProgress = isQuestionGen && hasProgressData && (status === "start" || status === "progress" || status === "complete");
                    
                    return (
                      <div key={module.id} className={`progress-module ${status}`}>
                        <span className="module-indicator">
                          {status === "complete" ? "✓" : status === "start" ? "⟳" : idx + 1}
                        </span>
                        <span className="module-name">{module.name}</span>
                        <span className="module-status">
                          {status === "complete" ? "完成" : status === "start" ? "进行中" : "等待中"}
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
                  {pipelineResults.retrieval?.status === "skipped" && (
                    <div className="result-item skipped">
                      <strong>检索：</strong>
                      模块待实现
                    </div>
                  )}
                  {pipelineResults.evaluation?.status === "skipped" && (
                    <div className="result-item skipped">
                      <strong>评测：</strong>
                      模块待实现
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Dashboard with Core Metrics */}
          {latestSummary && latestSummary.summary && (
            <div className="dashboard-section">
              <div className="section-header">
                <h3>核心指标</h3>
                <p className="section-subtitle">最新评测结果概览</p>
              </div>
              <div className="dashboard-metrics">
                <div className="metric-card">
                  <div className="metric-label">准确率</div>
                  <div className="metric-value">
                    {latestSummary.summary.accuracy_percentage?.toFixed(2) || "0.00"}%
                  </div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">召回率</div>
                  <div className="metric-value">
                    {latestSummary.summary.recall_percentage?.toFixed(2) || "0.00"}%
                  </div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">总问题数</div>
                  <div className="metric-value">
                    {latestSummary.summary.total_questions || 0}
                  </div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">正确数</div>
                  <div className="metric-value">
                    {latestSummary.summary.correct_count || 0}
                  </div>
                </div>
              </div>
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

