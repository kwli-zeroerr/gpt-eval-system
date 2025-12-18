import { useEffect, useState } from "react";

interface EvaluationResult {
  results_csv_path: string;
  summary_json_path: string;
  summary: {
    total_questions: number;
    correct_count: number;
    accuracy: number;
    recall: number;
    accuracy_percentage: number;
    recall_percentage: number;
  };
  total_questions: number;
  total_time: number;
}

interface CsvFile {
  path: string;
  filename: string;
  size: number;
  modified_at: string;
}

function Evaluation() {
  const [csvFiles, setCsvFiles] = useState<CsvFile[]>([]);
  const [selectedCsv, setSelectedCsv] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [progress, setProgress] = useState({ current: 0, total: 0, percentage: 0 });

  useEffect(() => {
    fetchCsvFiles();
  }, []);

  const fetchCsvFiles = async () => {
    try {
      // 评估模块从 data/retrieval/ 读取 CSV（检索的输出，包含答案）
      const response = await fetch("/api/data/retrieval-csv-files");
      const data = await response.json();
      setCsvFiles(data.csv_files || []);
    } catch (e) {
      setError("获取 CSV 文件列表失败");
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
        body: JSON.stringify({ csv_path: selectedCsv }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "评测失败");
      }

      const data = await response.json();
      setResult(data);
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

  return (
    <div className="evaluation-module">
      <h2>评测模块</h2>
      <p>从带答案的 CSV 文件计算评测指标（准确率、召回率等）</p>

      {error && <div className="error-message">错误：{error}</div>}

      <div className="evaluation-form">
        <div className="form-group">
          <label htmlFor="csv-select">选择包含答案的 CSV 文件：</label>
          {csvFiles.length === 0 ? (
            <p className="no-files">
              暂无可用 CSV 文件。请先在"检索"模块运行检索，生成带答案的 CSV 文件。
            </p>
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
          className="btn-primary"
        >
          {loading ? "评测中..." : "开始评测"}
        </button>
      </div>

      {loading && progress.total > 0 && (
        <div className="progress-bar-container">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${progress.percentage}%` }}
            />
          </div>
          <p>
            进度: {progress.current} / {progress.total} ({progress.percentage}%)
          </p>
        </div>
      )}

      {result && (
        <div className="evaluation-result">
          <h3>评测结果</h3>
          <div className="result-summary">
            <div className="summary-card">
              <h4>总体指标</h4>
              <p>
                <strong>总问题数：</strong> {result.summary.total_questions}
              </p>
              <p>
                <strong>正确数：</strong> {result.summary.correct_count}
              </p>
              <p>
                <strong>准确率：</strong>{" "}
                {result.summary.accuracy_percentage.toFixed(2)}%
              </p>
              <p>
                <strong>召回率：</strong>{" "}
                {result.summary.recall_percentage.toFixed(2)}%
              </p>
              <p>
                <strong>耗时：</strong> {result.total_time.toFixed(2)} 秒
              </p>
            </div>
          </div>
          <div className="result-actions">
            <button onClick={downloadResults} className="btn-primary">
              下载详细结果 CSV
            </button>
            <button onClick={downloadSummary} className="btn-secondary">
              下载摘要 JSON
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default Evaluation;
