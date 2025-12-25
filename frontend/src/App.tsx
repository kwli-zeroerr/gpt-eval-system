import { useEffect, useMemo, useState, useRef, memo, useCallback } from "react";
import { List } from "react-window";
import { fetchCategories, generateQuestions } from "./api";
import { Category, QuestionItem } from "./types";

type PromptMap = Record<string, string>;

// Memoized Question Item Component for better performance
const QuestionItemComponent = memo(({ 
  question, 
  index, 
  refIndex 
}: { 
  question: QuestionItem; 
  index: number; 
  refIndex: number | null;
}) => {
  return (
    <div className="question-item">
      <span className="question-number">{index}.</span>
      <div className="question-content">
        <span className="question-text">
          {question.text}
          {refIndex && (
            <sup className="reference-sup">{refIndex}</sup>
          )}
        </span>
      </div>
    </div>
  );
});

QuestionItemComponent.displayName = "QuestionItemComponent";

// Virtual List Row Component
const VirtualRow = memo(({ 
  index, 
  style, 
  rowProps 
}: { 
  index: number; 
  style: React.CSSProperties; 
  rowProps: { 
    questions: QuestionItem[]; 
    startIdx: number; 
    refMap: Map<string, number>;
  };
}) => {
  const { questions, startIdx, refMap } = rowProps;
  const question = questions[index];
  if (!question) return null;
  
  const hasReference = question.reference && question.reference.trim();
  const refIndex = hasReference && question.reference ? refMap.get(question.reference) || null : null;
  const globalIdx = startIdx + index + 1;
  
  return (
    <div style={style}>
      <QuestionItemComponent 
        question={question} 
        index={globalIdx} 
        refIndex={refIndex}
      />
    </div>
  );
});

VirtualRow.displayName = "VirtualRow";

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
  const [itemsPerPage, setItemsPerPage] = useState(50); // Increased from 10 to 50
  const [questionGenProgress, setQuestionGenProgress] = useState<{
    activeCategories: string[]; // Support multiple concurrent categories
    completedCategories: string[];
    totalCategories: number;
  } | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const listRefs = useRef<Record<string, any>>({});

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

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
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

  // Filtered and searched results - optimized with useMemo
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

  // Memoized filtered questions per category
  const filteredByCategory = useMemo(() => {
    const filtered: Record<string, QuestionItem[]> = {};
    Object.entries(groupedResults).forEach(([catId, questions]) => {
      const filteredQuestions = questions.filter((q) => {
        if (filterCategory !== "all" && q.category !== filterCategory) {
          return false;
        }
        if (searchQuery.trim()) {
          return q.text.toLowerCase().includes(searchQuery.toLowerCase());
        }
        return true;
      });
      if (filteredQuestions.length > 0) {
        filtered[catId] = filteredQuestions;
      }
    });
    return filtered;
  }, [groupedResults, filterCategory, searchQuery]);

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
    // Initialize question generation progress
    setQuestionGenProgress({
      activeCategories: [],
      completedCategories: [],
      totalCategories: 6,
    });
    
    // Close existing WebSocket if any
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    
    // Use WebSocket for real-time progress
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/generate`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    
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
        // Initialize question generation progress when starting
        setQuestionGenProgress({
          activeCategories: data.categories || [],
          completedCategories: [],
          totalCategories: data.categories?.length || 6,
        });
      } else if (data.type === 'progress') {
        setProgress({
          current: data.current,
          total: data.total,
          category: data.category || (data.activeCategories && data.activeCategories.length > 0 ? data.activeCategories.join(', ') : ''),
          percentage: data.percentage,
          elapsed: data.elapsed,
        });
        // Update question generation progress - support multiple concurrent categories
        if (data.activeCategories && Array.isArray(data.activeCategories)) {
          setQuestionGenProgress((prev) => {
            if (!prev) {
              return {
                activeCategories: data.activeCategories,
                completedCategories: [],
                totalCategories: 6,
              };
            }
            // Update active categories list for concurrent processing
            return {
              ...prev,
              activeCategories: data.activeCategories,
            };
          });
        } else if (data.category) {
          // Fallback: single category mode
          setQuestionGenProgress((prev) => {
            if (!prev) {
              return {
                activeCategories: [data.category],
                completedCategories: [],
                totalCategories: 6,
              };
            }
            // Add category to active list if not already there
            const active = prev.activeCategories.includes(data.category)
              ? prev.activeCategories
              : [...prev.activeCategories, data.category];
            return {
              ...prev,
              activeCategories: active,
            };
          });
        }
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
        // Update question generation progress - mark category as completed
        setQuestionGenProgress((prev) => {
          if (!prev) {
            return {
              activeCategories: [],
              completedCategories: [data.category],
              totalCategories: 6,
            };
          }
          const completed = [...prev.completedCategories];
          if (!completed.includes(data.category)) {
            completed.push(data.category);
          }
          // Remove from active categories when completed
          const active = prev.activeCategories.filter(cat => cat !== data.category);
          return {
            ...prev,
            completedCategories: completed,
            activeCategories: active,
          };
        });
      } else if (data.type === 'complete') {
        setResults(data.questions || []);
        setCategoryTimes(data.category_times || {});
        setTotalTime(data.total_time || null);
        setLoading(false);
        setProgress(null);
        // Clear active categories when complete
        setQuestionGenProgress((prev) => {
          if (!prev) return null;
          return {
            ...prev,
            activeCategories: [], // Clear active categories when all done
          };
        });
        if (wsRef.current) {
          wsRef.current.close();
          wsRef.current = null;
        }
      } else if (data.type === 'error') {
        setError(data.message || '生成失败');
        setLoading(false);
        setProgress(null);
        setQuestionGenProgress(null);
        if (wsRef.current) {
          wsRef.current.close();
          wsRef.current = null;
        }
      }
    };
    
    ws.onerror = (error) => {
      setError('WebSocket连接错误');
      setLoading(false);
      setProgress(null);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    
    ws.onclose = () => {
      // Connection closed
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
    };
  };

  return (
    <div className="question-gen-module">
      <h2>问题生成</h2>
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

      {/* S1-S6 Category Progress Bar */}
      {questionGenProgress && loading && (
        <div className="question-gen-progress">
          <div className="qg-progress-title">问题生成进度 (S1-S6)</div>
          <div className="qg-categories-bar">
            {["S1", "S2", "S3", "S4", "S5", "S6"].map((cat) => {
              const isCompleted = questionGenProgress.completedCategories.includes(cat);
              const isActive = questionGenProgress.activeCategories.includes(cat);
              return (
                <div
                  key={cat}
                  className={`qg-category-item ${isCompleted ? "completed" : ""} ${isActive ? "active" : ""}`}
                  title={isCompleted ? `${cat} 已完成` : isActive ? `正在生成 ${cat}` : `${cat} 等待中`}
                >
                  {isCompleted ? "✓" : isActive ? "⟳" : cat}
                </div>
              );
            })}
          </div>
          {questionGenProgress.activeCategories.length > 0 ? (
            <div className="qg-current-status">
              正在生成 {questionGenProgress.activeCategories.join('、')} 类别问题...
            </div>
          ) : (
            <div className="qg-current-status">
              准备生成问题...
            </div>
          )}
        </div>
      )}

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

      {/* Results Section with Search and Filter - Hidden in question generation module */}
      {false && results.length > 0 && (
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
              <label className="items-per-page-label">
                每页显示：
                <select
                  value={itemsPerPage}
                  onChange={(e) => {
                    setItemsPerPage(Number(e.target.value));
                    // Reset all category pages when changing items per page
                    setCategoryPages({});
                  }}
                  className="items-per-page-select"
                >
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={200}>200</option>
                  <option value={500}>500</option>
                </select>
              </label>
              <button onClick={exportToFile} className="export-btn">
                导出为文件
              </button>
            </div>
          </div>

          {/* Grouped Results by Category with Virtual Scrolling */}
          <div className="results-grouped">
            {Object.entries(filteredByCategory).map(([catId, filtered]) => {
              const cat = categories.find((c) => c.id === catId);
              if (!cat || filtered.length === 0) return null;

              // Pagination: if > itemsPerPage questions, show pagination
              const needsPagination = filtered.length > itemsPerPage;
              const currentPage = categoryPages[catId] || 1;
              const totalPages = Math.ceil(filtered.length / itemsPerPage);
              const startIdx = needsPagination ? (currentPage - 1) * itemsPerPage : 0;
              const endIdx = needsPagination ? startIdx + itemsPerPage : filtered.length;
              const paginatedQuestions = filtered.slice(startIdx, endIdx);
              
              // Build reference map: reference -> index
              const refMap = (() => {
                const map = new Map<string, number>();
                let refCounter = 1;
                paginatedQuestions.forEach((q) => {
                  if (q.reference && q.reference.trim() && !map.has(q.reference)) {
                    map.set(q.reference, refCounter++);
                  }
                });
                return map;
              })();
              
              // Use virtual scrolling for large lists (>= 50 items)
              const useVirtualScroll = paginatedQuestions.length >= 50;
              const itemHeight = 60; // Estimated height per item
              const listHeight = Math.min(600, paginatedQuestions.length * itemHeight); // Max 600px height
              
              return (
                <div key={catId} className="category-group">
                  <div className="category-header">
                    <h3>
                      {catId} - {cat.title} ({filtered.length} 个)
                    </h3>
                  </div>
                  <div className="questions-list">
                    {useVirtualScroll ? (
                      <List
                        ref={(ref: any) => {
                          listRefs.current[catId] = ref;
                        }}
                        height={listHeight}
                        width="100%"
                        itemCount={paginatedQuestions.length}
                        itemSize={itemHeight}
                        // @ts-ignore - react-window type definition issue
                        children={({ index, style }: { index: number; style: React.CSSProperties }) => (
                          <VirtualRow
                            index={index}
                            style={style}
                            rowProps={{
                              questions: paginatedQuestions,
                              startIdx,
                              refMap,
                            }}
                          />
                        )}
                      />
                    ) : (
                      // For smaller lists, render normally
                      paginatedQuestions.map((q, idx) => {
                        const hasReference = q.reference && q.reference.trim();
                        const refIndex = hasReference && q.reference ? refMap.get(q.reference) || null : null;
                        const globalIdx = startIdx + idx + 1;
                        
                        return (
                          <QuestionItemComponent
                            key={startIdx + idx}
                            question={q}
                            index={globalIdx}
                            refIndex={refIndex}
                          />
                        );
                      })
                    )}
                    {/* Reference notes at the bottom */}
                    {(() => {
                      const refEntries = Array.from(refMap.entries()).sort((a, b) => a[1] - b[1]);
                      if (refEntries.length > 0) {
                        // Helper function to format reference for display
                        const formatReference = (ref: string): string => {
                          if (!ref) return '';
                          
                          // Handle multiple references separated by ';'
                          const refs = ref.split(';').map(r => r.trim()).filter(r => r);
                          if (refs.length === 0) return '';
                          
                          const formattedRefs = refs.map(r => {
                            // Check if reference is in format "<source_file>|<heading>"
                            const parts = r.split('|');
                            if (parts.length >= 2) {
                              // Return the heading part (everything after the first |)
                              return parts.slice(1).join('|').trim() || parts[0].trim();
                            }
                            // For old format (just numbers or simple text), return as is
                            return r.trim();
                          });
                          
                          // Join multiple references with '; '
                          return formattedRefs.join('; ');
                        };
                        
                        return (
                          <div className="reference-notes">
                            {refEntries.map(([ref, idx]) => {
                              const displayText = formatReference(ref);
                              return (
                              <div key={idx} className="reference-note">
                                  <sup>{idx}</sup> <span className="reference-text">{displayText || ref}</span>
                              </div>
                              );
                            })}
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
                        onClick={() => {
                          setCategoryPage(catId, currentPage - 1);
                          // Scroll to top of list when changing page
                          if (listRefs.current[catId]) {
                            listRefs.current[catId]?.scrollToItem(0);
                          }
                        }}
                        disabled={currentPage === 1}
                        className="page-btn"
                      >
                        上一页
                      </button>
                      <span className="page-info">
                        第 {currentPage} / {totalPages} 页
                      </span>
                      <input
                        type="number"
                        min={1}
                        max={totalPages}
                        value={currentPage}
                        onChange={(e) => {
                          const page = Math.max(1, Math.min(totalPages, Number(e.target.value)));
                          setCategoryPage(catId, page);
                          if (listRefs.current[catId]) {
                            listRefs.current[catId]?.scrollToItem(0);
                          }
                        }}
                        className="page-input"
                      />
                      <button
                        onClick={() => {
                          setCategoryPage(catId, currentPage + 1);
                          if (listRefs.current[catId]) {
                            listRefs.current[catId]?.scrollToItem(0);
                          }
                        }}
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
