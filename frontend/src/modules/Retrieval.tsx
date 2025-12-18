import { useEffect, useState } from "react";

interface RetrievalResult {
  output_csv_path: string;
  input_csv_path: string;
  total_questions: number;
  completed: number;
  failed: number;
  total_time: number;
}

interface CsvFile {
  path: string;
  filename: string;
  size: number;
  modified_at: string;
}

function Retrieval() {
  const [csvFiles, setCsvFiles] = useState<CsvFile[]>([]);
  const [selectedCsv, setSelectedCsv] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [progress, setProgress] = useState({ current: 0, total: 0, percentage: 0 });

  useEffect(() => {
    fetchCsvFiles();
  }, []);

  const fetchCsvFiles = async () => {
    try {
      // 检索模块从 data/export/ 读取 CSV（格式转换的输出）
      const response = await fetch("/api/data/csv-files");
      const data = await response.json();
      setCsvFiles(data.csv_files || []);
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

      const data = await response.json();
      setResult(data);
      
      // Refresh CSV files list after retrieval
      await fetchCsvFiles();
    } catch (e) {
      setError("检索失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const downloadResult = () => {
    if (result?.output_csv_path) {
      // 这里需要后端提供下载接口，暂时使用文件路径
      window.open(result.output_csv_path, "_blank");
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  };

  return (
    <div className="retrieval-module">
      <h2>检索模块</h2>
      <p>从 CSV 文件读取问题，调用 RagFlow API 获取答案，填充到 CSV</p>

      {error && <div className="error-message">错误：{error}</div>}

      <div className="retrieval-form">
        <div className="form-group">
          <label htmlFor="csv-select">选择 CSV 文件：</label>
          {csvFiles.length === 0 ? (
            <p className="no-files">暂无可用 CSV 文件。请先在"格式转换"模块生成 CSV 文件。</p>
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
          {loading ? "检索中..." : "开始检索"}
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
        <div className="retrieval-result">
          <h3>检索结果</h3>
          <div className="result-stats">
            <p>
              <strong>总问题数：</strong> {result.total_questions}
            </p>
            <p>
              <strong>成功：</strong> {result.completed}
            </p>
            <p>
              <strong>失败：</strong> {result.failed}
            </p>
            <p>
              <strong>耗时：</strong> {result.total_time.toFixed(2)} 秒
            </p>
            <p>
              <strong>输出文件：</strong> {result.output_csv_path}
            </p>
          </div>
          <button onClick={downloadResult} className="btn-primary">
            下载结果 CSV
          </button>
        </div>
      )}
    </div>
  );
}

export default Retrieval;
