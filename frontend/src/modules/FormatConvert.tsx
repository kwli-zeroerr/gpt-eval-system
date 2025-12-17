import { useEffect, useState } from "react";

interface LogFile {
  path: string;
  request_id: string;
  generated_at: string;
  total_questions: number;
}

function FormatConvert() {
  const [logs, setLogs] = useState<LogFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<string | null>(null);
  const [csvPreview, setCsvPreview] = useState<string[][]>([]);

  useEffect(() => {
    fetchLogs();
  }, []);

  const fetchLogs = async () => {
    try {
      const response = await fetch("/api/format/logs");
      const data = await response.json();
      setLogs(data.logs || []);
    } catch (e) {
      setError("获取日志文件失败");
    }
  };

  const handleConvert = async (logPath: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/format/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log_file_path: logPath }),
      });
      if (!response.ok) {
        throw new Error("转换失败");
      }
      const data = await response.json();
      
      // Fetch CSV preview
      const log = logs.find((l) => l.path === logPath);
      if (log) {
        await previewCSV(log.request_id);
      }
    } catch (e) {
      setError("转换失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const previewCSV = async (logId: string) => {
    try {
      // For preview, we'll convert and show first few rows
      const log = logs.find((l) => l.request_id.startsWith(logId));
      if (!log) return;
      
      // First convert if not already converted
      const convertResponse = await fetch("/api/format/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ log_file_path: log.path }),
      });
      if (!convertResponse.ok) {
        throw new Error("转换失败");
      }
      
      // Read CSV file (first 10 rows for preview)
      const csvResponse = await fetch(`/api/format/download/${logId}`);
      if (!csvResponse.ok) {
        throw new Error("下载失败");
      }
      const csvText = await csvResponse.text();
      const lines = csvText.split("\n").filter((line) => line.trim()).slice(0, 11); // Header + 10 rows
      const preview = lines.map((line) => {
        // Simple CSV parsing (handles quoted fields)
        const result: string[] = [];
        let current = "";
        let inQuotes = false;
        for (let i = 0; i < line.length; i++) {
          const char = line[i];
          if (char === '"') {
            inQuotes = !inQuotes;
          } else if (char === "," && !inQuotes) {
            result.push(current);
            current = "";
          } else {
            current += char;
          }
        }
        result.push(current);
        return result;
      });
      setCsvPreview(preview);
      setSelectedLog(logId);
    } catch (e) {
      setError("预览失败: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  const downloadCSV = (logId: string) => {
    window.open(`/api/format/download/${logId}`, "_blank");
  };

  return (
    <div className="format-convert-module">
      <h2>格式转换</h2>
      <p>将问题生成日志转换为 CSV 格式（question, answer, reference, type, theme）</p>

      {error && <div className="error-message">错误：{error}</div>}

      <div className="logs-list">
        <h3>可用日志文件</h3>
        {logs.length === 0 ? (
          <p>暂无日志文件</p>
        ) : (
          <table className="logs-table">
            <thead>
              <tr>
                <th>请求ID</th>
                <th>生成时间</th>
                <th>问题数量</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.request_id}>
                  <td>{log.request_id.substring(0, 8)}...</td>
                  <td>{new Date(log.generated_at).toLocaleString()}</td>
                  <td>{log.total_questions}</td>
                  <td>
                    <button
                      onClick={() => handleConvert(log.path)}
                      disabled={loading}
                      className="btn-primary"
                    >
                      转换为CSV
                    </button>
                    <button
                      onClick={() => previewCSV(log.request_id)}
                      className="btn-secondary"
                    >
                      预览
                    </button>
                    <button
                      onClick={() => downloadCSV(log.request_id)}
                      className="btn-secondary"
                    >
                      下载
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {csvPreview.length > 0 && (
        <div className="csv-preview">
          <h3>CSV 预览（前10行）</h3>
          <table className="csv-table">
            <thead>
              <tr>
                {csvPreview[0]?.map((header, idx) => (
                  <th key={idx}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {csvPreview.slice(1).map((row, idx) => (
                <tr key={idx}>
                  {row.map((cell, cellIdx) => (
                    <td key={cellIdx}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default FormatConvert;

