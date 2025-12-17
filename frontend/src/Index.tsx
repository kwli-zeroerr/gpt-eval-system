import { useState, useEffect, useRef } from "react";
import figlet from "figlet";
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
  const [currentStep, setCurrentStep] = useState<number>(0); // 0 = question-gen, 1 = format-convert, etc.
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineProgress, setPipelineProgress] = useState<Record<string, string>>({});
  const [pipelineResults, setPipelineResults] = useState<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [figletText, setFigletText] = useState<string>("");

  const activeModuleInfo = MODULES.find((m) => m.id === activeModule);

  // Show overview page when no specific module is selected, or show module details
  const showOverview = activeModule === "overview";

  // Generate figlet text on mount
  useEffect(() => {
    figlet("Now You See it", (err, data) => {
      if (err) {
        console.error("Figlet error:", err);
        return;
      }
      if (data) {
        setFigletText(data);
      }
    });
  }, []);

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
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
      // Send pipeline request with default settings
      ws.send(JSON.stringify({
        categories: ["S1", "S2", "S3", "S4", "S5", "S6"],
        per_category: 5,
        prompt_overrides: {},
        source_files: [],
      }));
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === "module_progress") {
        const { module, status, data: moduleData } = data;
        setPipelineProgress((prev) => ({
          ...prev,
          [module]: status,
        }));
        
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
            setCurrentStep(moduleIndex);
          } else if (status === "complete") {
            setCurrentStep(moduleIndex + 1);
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
      {/* Figlet Banner */}
      {figletText && (
        <div className="figlet-banner">
          <pre className="figlet-text">{figletText}</pre>
        </div>
      )}
      
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
              className={`module-tab ${activeModule === module.id ? "active" : ""} ${module.status}`}
              onClick={() => {
                setActiveModule(module.id);
                setCurrentStep(idx);
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
                    strokeDashoffset={`${2 * Math.PI * 45 * (1 - (currentStep + 1) / MODULES.length)}`}
                    transform="rotate(-90 50 50)"
                  />
                </svg>
                <div className="circular-progress-text">
                  <div className="progress-ratio">{currentStep + 1} of {MODULES.length}</div>
                </div>
              </div>
              <div className="circular-progress-info">
                <div className="current-step-name">
                  {currentStep < MODULES.length ? MODULES[currentStep]?.name : "全部完成"}
                </div>
                <div className="current-step-desc">
                  {currentStep < MODULES.length 
                    ? "正在处理中..." 
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
                    className={`progress-segment ${idx <= currentStep ? "completed" : ""} ${idx === currentStep ? "active" : ""}`}
                    onClick={() => {
                      setActiveModule(module.id);
                      setCurrentStep(idx);
                    }}
                  >
                    <div className="segment-content">
                      {module.name}
                    </div>
                    {idx < MODULES.length - 1 && (
                      <div className={`segment-arrow ${idx < currentStep ? "completed" : ""}`}></div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="quick-actions">
            <h3>快速操作</h3>
            <div className="action-buttons">
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
              <button
                className="action-btn"
                onClick={() => {
                  setActiveModule("question-gen");
                  setCurrentStep(0);
                }}
                disabled={pipelineRunning}
              >
                <span className="action-icon">📝</span>
                <span className="action-text">仅生成问题</span>
              </button>
              <button
                className="action-btn"
                onClick={() => {
                  setActiveModule("format-convert");
                  setCurrentStep(1);
                }}
                disabled={pipelineRunning}
              >
                <span className="action-icon">📄</span>
                <span className="action-text">仅转换格式</span>
              </button>
            </div>
            
            {/* Pipeline Progress */}
            {pipelineRunning && (
              <div className="pipeline-progress">
                <div className="progress-title">运行进度</div>
                <div className="progress-modules">
                  {MODULES.map((module, idx) => {
                    const moduleKey = module.id.replace("-", "_");
                    const status = pipelineProgress[moduleKey] || "pending";
                    return (
                      <div key={module.id} className={`progress-module ${status}`}>
                        <span className="module-indicator">
                          {status === "complete" ? "✓" : status === "start" ? "⟳" : idx + 1}
                        </span>
                        <span className="module-name">{module.name}</span>
                        <span className="module-status">
                          {status === "complete" ? "完成" : status === "start" ? "进行中" : "等待中"}
                        </span>
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

          <div className="overview-stats">
            <div className="stat-card">
              <div className="stat-value">{MODULES.filter(m => m.status === "completed").length}</div>
              <div className="stat-label">已完成模块</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{MODULES.filter(m => m.status === "pending").length}</div>
              <div className="stat-label">待开发模块</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{currentStep + 1}</div>
              <div className="stat-label">当前进度</div>
            </div>
          </div>
        </div>
      ) : (
        /* Module Detail Page */
        <div className="module-content">
          {activeModuleInfo && <activeModuleInfo.component />}
        </div>
      )}
    </div>
  );
}

export default Index;

