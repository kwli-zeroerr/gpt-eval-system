import { useEffect, useState } from "react";

interface LogFile {
  path: string;
  request_id: string;
  generated_at: string;
  total_questions: number;
  csv_exists?: boolean;
}

function FormatConvert() {
  const [logs, setLogs] = useState<LogFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<string | null>(null);
  const [csvPreview, setCsvPreview] = useState<string[][]>([]);
  const [csvFullData, setCsvFullData] = useState<string[][]>([]);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [rowsPerPage, setRowsPerPage] = useState<number>(20);
  const [deleteConfirm, setDeleteConfirm] = useState<{ show: boolean; logId: string | null }>({
    show: false,
    logId: null,
  });

  useEffect(() => {
    fetchLogs();
  }, []);

  const fetchLogs = async () => {
    try {
      const response = await fetch("/api/format/logs");
      const data = await response.json();
      const logsList = data.logs || [];
      
      // Check CSV existence for each log
      const logsWithCsvStatus = await Promise.all(
        logsList.map(async (log: LogFile) => {
          try {
            const checkResponse = await fetch(`/api/format/check-csv/${log.request_id}`);
            const checkData = await checkResponse.json();
            return { ...log, csv_exists: checkData.exists || false };
          } catch {
            return { ...log, csv_exists: false };
          }
        })
      );
      
      setLogs(logsWithCsvStatus);
    } catch (e) {
      setError("获取日志文件失败");
    }
  };

  const handleDelete = async (logId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/format/logs/${logId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "删除失败");
      }
      
      // Remove from local state
      setLogs((prevLogs) => prevLogs.filter((l) => !l.request_id.startsWith(logId)));
      
      // Clear preview if deleted log was being previewed
      if (selectedLog === logId) {
        setCsvPreview([]);
        setCsvFullData([]);
        setSelectedLog(null);
      }
      
      // Close confirmation dialog
      setDeleteConfirm({ show: false, logId: null });
    } catch (e) {
      setError("删除失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  const handleConvertAndDownload = async (logId: string) => {
    const log = logs.find((l) => l.request_id.startsWith(logId));
    if (!log) return;
    
    setLoading(true);
    setError(null);
    
    try {
      // Download endpoint will convert if needed, then download
      const link = document.createElement("a");
      link.href = `/api/format/download/${logId}`;
      link.download = `questions_${logId}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Update CSV status after a short delay
      setTimeout(async () => {
        try {
          const checkResponse = await fetch(`/api/format/check-csv/${logId}`);
          const checkData = await checkResponse.json();
          if (checkData.exists) {
            setLogs((prevLogs) =>
              prevLogs.map((l) =>
                l.request_id.startsWith(logId) ? { ...l, csv_exists: true } : l
              )
            );
          }
        } catch {
          // Ignore errors
        }
        setLoading(false);
      }, 1500);
    } catch (e) {
      setError("转换并下载失败: " + (e instanceof Error ? e.message : String(e)));
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
      
      // Read full CSV file
      const csvResponse = await fetch(`/api/format/download/${logId}`);
      if (!csvResponse.ok) {
        throw new Error("下载失败");
      }
      const csvText = await csvResponse.text();
      
      // Parse CSV with proper handling of quoted fields containing newlines
      const parseCSV = (text: string): string[][] => {
        const rows: string[][] = [];
        let currentRow: string[] = [];
        let currentField = "";
        let inQuotes = false;
        
        for (let i = 0; i < text.length; i++) {
          const char = text[i];
          const nextChar = i + 1 < text.length ? text[i + 1] : null;
          
          if (char === '"') {
            if (inQuotes && nextChar === '"') {
              // Escaped quote (double quote)
              currentField += '"';
              i++; // Skip next quote
            } else {
              // Toggle quote state
            inQuotes = !inQuotes;
            }
          } else if (char === ',' && !inQuotes) {
            // Field separator
            currentRow.push(currentField);
            currentField = "";
          } else if ((char === '\n' || char === '\r') && !inQuotes) {
            // Row separator (only if not in quotes)
            if (char === '\r' && nextChar === '\n') {
              i++; // Skip \n after \r
            }
            if (currentField || currentRow.length > 0) {
              currentRow.push(currentField);
              if (currentRow.length > 0 && currentRow.some(f => f.trim())) {
                rows.push(currentRow);
              }
              currentRow = [];
              currentField = "";
            }
          } else {
            // Regular character
            currentField += char;
          }
        }
        
        // Add last field and row
        if (currentField || currentRow.length > 0) {
          currentRow.push(currentField);
          if (currentRow.length > 0 && currentRow.some(f => f.trim())) {
            rows.push(currentRow);
          }
        }
        
        return rows;
      };
      
      const allRows = parseCSV(csvText);
      setCsvFullData(allRows);
      setCurrentPage(1);
      setSelectedLog(logId);
    } catch (e) {
      setError("预览失败: " + (e instanceof Error ? e.message : String(e)));
    }
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
                      onClick={() => previewCSV(log.request_id)}
                      className="btn-primary"
                      disabled={loading}
                    >
                      预览
                    </button>
                    <button
                      onClick={() => handleConvertAndDownload(log.request_id)}
                      disabled={loading}
                      className="btn-action"
                      title={log.csv_exists ? "下载已存在的CSV文件" : "转换并下载CSV文件"}
                    >
                      转换并下载
                    </button>
                    <button
                      onClick={() => setDeleteConfirm({ show: true, logId: log.request_id })}
                      disabled={loading}
                      className="btn-danger"
                      title="删除日志文件"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {csvFullData.length > 0 && (
        <div className="csv-preview">
          <div className="csv-preview-header">
            <h3>CSV 预览</h3>
            <div className="csv-preview-controls">
              <label>
                每页显示：
                <select
                  value={rowsPerPage}
                  onChange={(e) => {
                    setRowsPerPage(Number(e.target.value));
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
                共 {csvFullData.length - 1} 行数据（不含表头）
              </span>
            </div>
          </div>
          
          {/* Pagination */}
          {csvFullData.length > rowsPerPage + 1 && (
            <div className="csv-pagination">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="page-btn"
              >
                上一页
              </button>
              <span className="page-info">
                第 {currentPage} / {Math.ceil((csvFullData.length - 1) / rowsPerPage)} 页
              </span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(Math.ceil((csvFullData.length - 1) / rowsPerPage), p + 1))}
                disabled={currentPage >= Math.ceil((csvFullData.length - 1) / rowsPerPage)}
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
                  {csvFullData[0]?.map((header, idx) => (
                  <th key={idx}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
                {csvFullData
                  .slice(1)
                  .slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage)
                  .map((row, idx) => (
                    <tr key={(currentPage - 1) * rowsPerPage + idx}>
                      {row.map((cell, cellIdx) => {
                        // Check if this is the question column (index 0) and contains multiple choice format
                        const isQuestionColumn = cellIdx === 0;
                        const hasMultipleChoice = isQuestionColumn && cell.includes('\n') && /[A-D]\):/.test(cell);
                        
                        return (
                          <td 
                            key={cellIdx}
                            className={hasMultipleChoice ? "csv-cell-multichoice" : ""}
                          >
                            {hasMultipleChoice ? (
                              <div className="multichoice-content">
                                {cell.split('\n').map((line, lineIdx) => (
                                  <div key={lineIdx} className={lineIdx === 0 ? "multichoice-question" : "multichoice-option"}>
                                    {line}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              cell
                            )}
                          </td>
                        );
                      })}
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          
          {/* Bottom Pagination */}
          {csvFullData.length > rowsPerPage + 1 && (
            <div className="csv-pagination">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="page-btn"
              >
                上一页
              </button>
              <span className="page-info">
                第 {currentPage} / {Math.ceil((csvFullData.length - 1) / rowsPerPage)} 页
              </span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(Math.ceil((csvFullData.length - 1) / rowsPerPage), p + 1))}
                disabled={currentPage >= Math.ceil((csvFullData.length - 1) / rowsPerPage)}
                className="page-btn"
              >
                下一页
              </button>
            </div>
          )}
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      {deleteConfirm.show && (
        <div className="modal-overlay" onClick={() => setDeleteConfirm({ show: false, logId: null })}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>确认删除</h3>
            </div>
            <div className="modal-body">
              <p>确定要删除此日志文件吗？</p>
              <p className="modal-warning">
                <strong>警告：</strong>此操作将删除以下文件：
              </p>
              <ul className="modal-file-list">
                <li>JSON 日志文件 (data/frontend/)</li>
                <li>TXT 日志文件 (data/backend/)</li>
                <li>CSV 文件 (data/export/) - 如果存在</li>
              </ul>
              <p className="modal-warning-text">此操作无法撤销！</p>
            </div>
            <div className="modal-footer">
              <button
                onClick={() => deleteConfirm.logId && handleDelete(deleteConfirm.logId)}
                className="btn-danger"
                disabled={loading}
              >
                {loading ? "删除中..." : "确认删除"}
              </button>
              <button
                onClick={() => setDeleteConfirm({ show: false, logId: null })}
                className="btn-secondary"
                disabled={loading}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default FormatConvert;

