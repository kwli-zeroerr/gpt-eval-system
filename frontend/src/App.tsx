import { useEffect, useMemo, useState } from "react";
import { fetchCategories, generateQuestions } from "./api";
import { Category, QuestionItem } from "./types";

type PromptMap = Record<string, string>;

function App() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [promptOverrides, setPromptOverrides] = useState<PromptMap>({});
  const [perCat, setPerCat] = useState(5);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<QuestionItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterCategory, setFilterCategory] = useState<string | "all">("all");
  const [progress, setProgress] = useState<{
    current: number;
    total: number;
    category: string;
    percentage: number;
    elapsed?: number;
  } | null>(null);
  const [categoryTimes, setCategoryTimes] = useState<Record<string, number>>({});
  const [totalTime, setTotalTime] = useState<number | null>(null);
  const [categoryPages, setCategoryPages] = useState<Record<string, number>>({});

  useEffect(() => {
    fetchCategories()
      .then((cats) => {
        setCategories(cats);
        setPromptOverrides(
          Object.fromEntries(cats.map((c) => [c.id, c.default_prompt]))
        );
        if (cats.length > 0) setActiveTab(cats[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // 所有分类都会被使用
  const payloadCategories = useMemo(
    () => categories.map((c) => c.id),
    [categories]
  );

  // Group results by category
  const groupedResults = useMemo(() => {
    const grouped: Record<string, QuestionItem[]> = {};
    results.forEach((q) => {
      if (!grouped[q.category]) {
        grouped[q.category] = [];
      }
      grouped[q.category].push(q);
    });
    return grouped;
  }, [results]);

  // Filtered and searched results
  const filteredResults = useMemo(() => {
    let filtered = results;

    // Filter by category
    if (filterCategory !== "all") {
      filtered = filtered.filter((q) => q.category === filterCategory);
    }

    // Search
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter((q) =>
        q.text.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [results, filterCategory, searchQuery]);

  const onPromptChange = (id: string, value: string) => {
    setPromptOverrides((prev) => ({ ...prev, [id]: value }));
  };

  const setCategoryPage = (catId: string, page: number) => {
    setCategoryPages((prev) => ({ ...prev, [catId]: page }));
  };

  const exportToFile = () => {
    const lines: string[] = [];
    Object.entries(groupedResults).forEach(([catId, questions]) => {
      const cat = categories.find((c) => c.id === catId);
      questions.forEach((q) => {
        const ref = q.reference || '';
        const line = `${q.text}-${catId}.${cat?.title || catId}-${ref}`;
        lines.push(line);
      });
    });
    
    const content = lines.join('\n');
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `questions_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const onGenerate = async () => {
    setLoading(true);
    setError(null);
    setProgress(null);
    setResults([]);
    setCategoryTimes({});
    setTotalTime(null);
    
    // Use WebSocket for real-time progress
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/generate`;
    const ws = new WebSocket(wsUrl);
    
    ws.onopen = () => {
      // Send request
      ws.send(JSON.stringify({
        categories: payloadCategories,
        per_category: perCat,
        prompt_overrides: promptOverrides,
        source_files: [],
      }));
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'start') {
        setProgress({
          current: 0,
          total: data.total,
          category: '',
          percentage: 0,
        });
      } else if (data.type === 'progress') {
        setProgress({
          current: data.current,
          total: data.total,
          category: data.category,
          percentage: data.percentage,
          elapsed: data.elapsed,
        });
      } else if (data.type === 'category_complete') {
        // Real-time display: add questions for this category immediately
        const newQuestions = (data.questions || []).map((q: any) => ({
          category: q.category,
          text: q.text,
          reference: q.reference || '',
        }));
        setResults((prev) => {
          // Remove existing questions for this category and add new ones
          const filtered = prev.filter((q) => q.category !== data.category);
          return [...filtered, ...newQuestions];
        });
        // Initialize page to 1 for this category
        setCategoryPages((prev) => ({ ...prev, [data.category]: 1 }));
      } else if (data.type === 'complete') {
        setResults(data.questions || []);
        setCategoryTimes(data.category_times || {});
        setTotalTime(data.total_time || null);
        setLoading(false);
        setProgress(null);
        ws.close();
      } else if (data.type === 'error') {
        setError(data.message || '生成失败');
        setLoading(false);
        setProgress(null);
        ws.close();
      }
    };
    
    ws.onerror = (error) => {
      setError('WebSocket连接错误');
      setLoading(false);
      setProgress(null);
    };
    
    ws.onclose = () => {
      // Connection closed
    };
  };

  return (
    <div className="page">
      <h1>问题生成（可配置分类与 Prompt）</h1>
      <p>调整 Prompt 与数量；默认每类 5 条，所有分类都会生成问题。</p>

      <div className="controls">
        <label>
          每类数量：
          <input
            type="number"
            min={1}
            value={perCat}
            onChange={(e) => setPerCat(Number(e.target.value))}
          />
        </label>
        <button onClick={onGenerate} disabled={loading || categories.length === 0}>
          {loading ? "生成中..." : "生成问题"}
        </button>
        <span>{categories.length} 个分类</span>
      </div>

      {/* Progress Bar */}
      {progress && (
        <div className="progress-container">
          <div className="progress-header">
            <span>
              正在生成 {progress.category} 问题 ({progress.current}/{progress.total})
              {progress.elapsed !== undefined && ` - 已用时: ${progress.elapsed.toFixed(1)}s`}
            </span>
            <span className="progress-percentage">{progress.percentage}%</span>
          </div>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${progress.percentage}%` }}
            />
          </div>
        </div>
      )}

      {/* Timing Summary */}
      {totalTime !== null && (
        <div className="timing-summary">
          <div className="timing-header">
            <strong>生成耗时统计：</strong>
            <span className="total-time">总耗时: {totalTime.toFixed(2)}秒</span>
          </div>
          <div className="category-timings">
            {Object.entries(categoryTimes).map(([catId, time]) => (
              <span key={catId} className="category-time">
                {catId}: {time.toFixed(2)}s
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="tabs">
        {categories.map((cat) => (
          <button
            key={cat.id}
            className={`tab ${activeTab === cat.id ? "active" : ""}`}
            onClick={() => setActiveTab(cat.id)}
          >
            {cat.id} · {cat.title}
          </button>
        ))}
      </div>

      {activeTab && (
        <div className="card">
          {categories
            .filter((c) => c.id === activeTab)
            .map((cat) => (
              <div key={cat.id} className="prompt-editor">
                <div className="category-header-info">
                  <strong>{cat.id}</strong> {cat.title}
                </div>
                <p className="category-description">{cat.description}</p>
                <div className="prompt-hint">
                  <div className="prompt-hint-content">
                    {cat.id === "S4" || cat.id === "S5" ? (
                      <p>说明：<code>{`{input_1}`}</code>、<code>{`{input_2}`}</code>、<code>{`{input_3}`}</code> 为文档片段占位符</p>
                    ) : (
                      <p>说明：<code>{`{input}`}</code> 为文档片段占位符</p>
                    )}
                  </div>
                </div>
                <label className="prompt-label">Prompt 模板：</label>
                <textarea
                  className="prompt-textarea"
                  value={promptOverrides[cat.id] ?? ""}
                  onChange={(e) => onPromptChange(cat.id, e.target.value)}
                  placeholder="输入或修改 Prompt 模板..."
                />
              </div>
            ))}
        </div>
      )}

      {error && (
        <div className="error-message">错误：{error}</div>
      )}

      {/* Results Section with Search and Filter */}
      {results.length > 0 && (
        <div className="results-section">
          <div className="results-header">
            <h2>生成结果 ({results.length} 个问题)</h2>
            <div className="results-controls">
              <input
                type="text"
                placeholder="搜索问题..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="search-input"
              />
              <select
                value={filterCategory}
                onChange={(e) => setFilterCategory(e.target.value)}
                className="filter-select"
              >
                <option value="all">所有分类</option>
                {categories.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.id} - {cat.title}
                  </option>
                ))}
              </select>
              <button onClick={exportToFile} className="export-btn">
                导出为文件
              </button>
            </div>
          </div>

          {/* Grouped Results by Category */}
          <div className="results-grouped">
            {Object.entries(groupedResults).map(([catId, questions]) => {
              const cat = categories.find((c) => c.id === catId);
              const filtered = questions.filter((q) => {
                if (filterCategory !== "all" && q.category !== filterCategory) {
                  return false;
                }
                if (searchQuery.trim()) {
                  return q.text.toLowerCase().includes(searchQuery.toLowerCase());
                }
                return true;
              });

              if (filtered.length === 0) return null;

              // Pagination: if > 10 questions, show pagination
              const itemsPerPage = 10;
              const needsPagination = filtered.length > itemsPerPage;
              const currentPage = categoryPages[catId] || 1;
              const totalPages = Math.ceil(filtered.length / itemsPerPage);
              const startIdx = needsPagination ? (currentPage - 1) * itemsPerPage : 0;
              const endIdx = needsPagination ? startIdx + itemsPerPage : filtered.length;
              const paginatedQuestions = filtered.slice(startIdx, endIdx);
              
              return (
                <div key={catId} className="category-group">
                  <div className="category-header">
                    <h3>
                      {catId} - {cat?.title || catId} ({filtered.length} 个)
                    </h3>
                  </div>
                  <div className="questions-list">
                    {(() => {
                      // Build reference map: reference -> index
                      const refMap = new Map<string, number>();
                      let refCounter = 1;
                      paginatedQuestions.forEach((q) => {
                        if (q.reference && q.reference.trim() && !refMap.has(q.reference)) {
                          refMap.set(q.reference, refCounter++);
                        }
                      });
                      
                      return paginatedQuestions.map((q, idx) => {
                        const hasReference = q.reference && q.reference.trim();
                        const refIndex = hasReference ? refMap.get(q.reference) : null;
                        const globalIdx = startIdx + idx + 1;
                        
                        return (
                          <div key={startIdx + idx} className="question-item">
                            <span className="question-number">{globalIdx}.</span>
                            <div className="question-content">
                              <span className="question-text">
                                {q.text}
                                {refIndex && (
                                  <sup className="reference-sup">{refIndex}</sup>
                                )}
                              </span>
                            </div>
                          </div>
                        );
                      });
                    })()}
                    {/* Reference notes at the bottom */}
                    {(() => {
                      const refMap = new Map<string, number>();
                      let refCounter = 1;
                      paginatedQuestions.forEach((q) => {
                        if (q.reference && q.reference.trim() && !refMap.has(q.reference)) {
                          refMap.set(q.reference, refCounter++);
                        }
                      });
                      
                      const refEntries = Array.from(refMap.entries()).sort((a, b) => a[1] - b[1]);
                      if (refEntries.length > 0) {
                        return (
                          <div className="reference-notes">
                            {refEntries.map(([ref, idx]) => (
                              <div key={idx} className="reference-note">
                                <sup>{idx}</sup> {ref}
                              </div>
                            ))}
                          </div>
                        );
                      }
                      return null;
                    })()}
                  </div>
                  {/* Pagination controls */}
                  {needsPagination && (
                    <div className="pagination">
                      <button
                        onClick={() => setCategoryPage(catId, currentPage - 1)}
                        disabled={currentPage === 1}
                        className="page-btn"
                      >
                        上一页
                      </button>
                      <span className="page-info">
                        第 {currentPage} / {totalPages} 页
                      </span>
                      <button
                        onClick={() => setCategoryPage(catId, currentPage + 1)}
                        disabled={currentPage === totalPages}
                        className="page-btn"
                      >
                        下一页
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Summary Stats */}
          <div className="results-summary">
            <div className="stat-item">
              <strong>总计：</strong> {results.length} 个问题
            </div>
            {Object.entries(groupedResults).map(([catId, questions]) => (
              <div key={catId} className="stat-item">
                <strong>{catId}：</strong> {questions.length} 个
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
