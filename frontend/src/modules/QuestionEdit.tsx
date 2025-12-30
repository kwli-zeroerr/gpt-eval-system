import { useEffect, useState, useMemo, useRef } from "react";
import { useModuleState } from "../contexts/ModuleStateContext";
import { QuestionItem } from "../types";

interface LogFile {
  path: string;
  request_id: string;
  generated_at: string;
  total_questions: number;
  is_edited?: boolean;
  original_path?: string;
}

interface QuestionWithId extends QuestionItem {
  id: string; // Unique ID for editing
}

function QuestionEdit() {
  const { getModuleState, setModuleState } = useModuleState();
  const moduleId = "question_edit";
  
  // 从状态管理恢复状态
  const savedState = getModuleState(moduleId) || {};
  
  const [logs, setLogs] = useState<LogFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedLog, setSelectedLog] = useState<string | null>(savedState.selectedLog || null);
  const [questions, setQuestions] = useState<QuestionWithId[]>(savedState.questions || []);
  const [originalQuestions, setOriginalQuestions] = useState<QuestionWithId[]>([]);
  const [filterCategory, setFilterCategory] = useState<string | "all">("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editReference, setEditReference] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  
  // 保存状态到状态管理
  useEffect(() => {
    setModuleState(moduleId, {
      selectedLog,
      questions,
      filterCategory,
      searchQuery,
    });
  }, [selectedLog, questions, filterCategory, searchQuery, setModuleState]);
  
  // 加载日志列表
  useEffect(() => {
    fetchLogs();
  }, []);
  
  const fetchLogs = async () => {
    try {
      const response = await fetch("/api/questions/edit/logs");
      if (!response.ok) {
        throw new Error("获取日志列表失败");
      }
      const data = await response.json();
      setLogs(data.logs || []);
    } catch (e) {
      setError("加载日志列表失败: " + (e instanceof Error ? e.message : String(e)));
    }
  };
  
  // 加载问题
  const loadQuestions = async (requestId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/questions/edit/${requestId}?use_edited=true`);
      if (!response.ok) {
        throw new Error("加载问题失败");
      }
      const data = await response.json();
      
      // 为每个问题生成唯一ID
      const questionsWithId: QuestionWithId[] = (data.questions || []).map((q: QuestionItem, idx: number) => ({
        ...q,
        id: `${requestId}_${idx}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      }));
      
      setQuestions(questionsWithId);
      setOriginalQuestions(questionsWithId);
      setSelectedLog(requestId);
      setHasUnsavedChanges(false);
    } catch (e) {
      setError("加载问题失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };
  
  // 过滤和搜索
  const filteredQuestions = useMemo(() => {
    let filtered = questions;
    
    // 按类别筛选
    if (filterCategory !== "all") {
      filtered = filtered.filter((q) => q.category === filterCategory);
    }
    
    // 搜索
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter((q) =>
        q.text.toLowerCase().includes(query)
      );
    }
    
    return filtered;
  }, [questions, filterCategory, searchQuery]);
  
  // 开始编辑
  const startEdit = (question: QuestionWithId) => {
    setEditingId(question.id);
    setEditText(question.text);
    setEditCategory(question.category);
    setEditReference(question.reference || "");
  };
  
  // 保存编辑
  const saveEdit = () => {
    if (!editingId) return;
    
    setQuestions((prev) =>
      prev.map((q) =>
        q.id === editingId
          ? { ...q, text: editText, category: editCategory, reference: editReference }
          : q
      )
    );
    setHasUnsavedChanges(true);
    setEditingId(null);
    setEditText("");
    setEditCategory("");
    setEditReference("");
  };
  
  // 取消编辑
  const cancelEdit = () => {
    setEditingId(null);
    setEditText("");
    setEditCategory("");
    setEditReference("");
  };
  
  // 删除问题
  const deleteQuestion = (id: string) => {
    if (!confirm("确定要删除这个问题吗？")) return;
    
    setQuestions((prev) => prev.filter((q) => q.id !== id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    setHasUnsavedChanges(true);
  };
  
  // 批量删除
  const deleteSelected = () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`确定要删除选中的 ${selectedIds.size} 个问题吗？`)) return;
    
    setQuestions((prev) => prev.filter((q) => !selectedIds.has(q.id)));
    setSelectedIds(new Set());
    setHasUnsavedChanges(true);
  };
  
  // 添加问题
  const addQuestion = () => {
    const newQuestion: QuestionWithId = {
      id: `new_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      category: "S1",
      text: "",
      reference: "",
    };
    setQuestions((prev) => [...prev, newQuestion]);
    setEditingId(newQuestion.id);
    setEditText("");
    setEditCategory("S1");
    setEditReference("");
    setHasUnsavedChanges(true);
  };
  
  // 保存所有更改
  const saveAll = async () => {
    if (!selectedLog) {
      setError("请先选择一个日志文件");
      return;
    }
    
    setLoading(true);
    setError(null);
    try {
      // 移除ID（后端不需要）
      const questionsToSave = questions.map(({ id, ...q }) => q);
      
      const response = await fetch(`/api/questions/edit/${selectedLog}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          questions: questionsToSave,
          category_times: {},
          total_time: null,
        }),
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "保存失败");
      }
      
      const data = await response.json();
      setHasUnsavedChanges(false);
      setError(null);
      // 重新加载日志列表以显示新保存的文件
      await fetchLogs();
      // 重新加载问题以获取最新数据
      await loadQuestions(selectedLog);
    } catch (e) {
      setError("保存失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };
  
  // 重置
  const reset = () => {
    if (!confirm("确定要重置所有更改吗？未保存的更改将丢失。")) return;
    setQuestions([...originalQuestions]);
    setHasUnsavedChanges(false);
    setSelectedIds(new Set());
  };
  
  // 切换选择
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };
  
  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedIds.size === filteredQuestions.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredQuestions.map((q) => q.id)));
    }
  };
  
  // 统计信息
  const stats = useMemo(() => {
    const byCategory: Record<string, number> = {};
    questions.forEach((q) => {
      byCategory[q.category] = (byCategory[q.category] || 0) + 1;
    });
    return {
      total: questions.length,
      byCategory,
    };
  }, [questions]);
  
  return (
    <div className="question-edit-module">
      <h2>问题编辑</h2>
      <p>查看、筛选、编辑生成的问题。编辑后的结果将保存为新版本文件。</p>
      
      {error && <div className="error-message">错误：{error}</div>}
      
      {/* 日志文件选择 */}
      <div className="logs-selection">
        <h3>选择日志文件</h3>
        {logs.length === 0 ? (
          <p>暂无日志文件</p>
        ) : (
          <div className="logs-list">
            {logs.map((log) => (
              <div
                key={log.request_id}
                className={`log-item ${selectedLog === log.request_id ? "selected" : ""}`}
                onClick={() => loadQuestions(log.request_id)}
              >
                <div className="log-header">
                  <span className="log-id">{log.request_id.substring(0, 8)}...</span>
                  {log.is_edited && <span className="edited-badge">已编辑</span>}
                </div>
                <div className="log-info">
                  <span>{new Date(log.generated_at).toLocaleString()}</span>
                  <span>{log.total_questions} 个问题</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      
      {/* 问题编辑区域 */}
      {selectedLog && (
        <div className="questions-editor">
          <div className="editor-header">
            <div className="editor-stats">
              <span>总计: {stats.total} 个问题</span>
              {Object.entries(stats.byCategory).map(([cat, count]) => (
                <span key={cat}>
                  {cat}: {count}
                </span>
              ))}
            </div>
            <div className="editor-actions">
              {hasUnsavedChanges && (
                <span className="unsaved-indicator">未保存</span>
              )}
              <button onClick={reset} disabled={!hasUnsavedChanges}>
                重置
              </button>
              <button onClick={saveAll} disabled={!hasUnsavedChanges || loading}>
                {loading ? "保存中..." : "保存"}
              </button>
            </div>
          </div>
          
          {/* 筛选和搜索 */}
          <div className="filters">
            <div className="filter-group">
              <label>类别筛选：</label>
              <select
                value={filterCategory}
                onChange={(e) => setFilterCategory(e.target.value)}
              >
                <option value="all">全部</option>
                <option value="S1">S1</option>
                <option value="S2">S2</option>
                <option value="S3">S3</option>
                <option value="S4">S4</option>
                <option value="S5">S5</option>
                <option value="S6">S6</option>
              </select>
            </div>
            <div className="filter-group">
              <label>搜索：</label>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索问题文本..."
              />
            </div>
            <div className="filter-group">
              <button onClick={addQuestion}>添加问题</button>
              {selectedIds.size > 0 && (
                <button onClick={deleteSelected} className="delete-btn">
                  删除选中 ({selectedIds.size})
                </button>
              )}
            </div>
          </div>
          
          {/* 问题列表 */}
          <div className="questions-list">
            {filteredQuestions.length === 0 ? (
              <p>没有匹配的问题</p>
            ) : (
              <>
                <div className="list-header">
                  <input
                    type="checkbox"
                    checked={selectedIds.size === filteredQuestions.length && filteredQuestions.length > 0}
                    onChange={toggleSelectAll}
                  />
                  <span>全选</span>
                </div>
                {filteredQuestions.map((question, idx) => (
                  <div key={question.id} className="question-item-editable">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(question.id)}
                      onChange={() => toggleSelect(question.id)}
                    />
                    <span className="question-number">{idx + 1}.</span>
                    {editingId === question.id ? (
                      <div className="edit-form">
                        <div className="edit-row">
                          <label>类别：</label>
                          <select
                            value={editCategory}
                            onChange={(e) => setEditCategory(e.target.value)}
                          >
                            <option value="S1">S1</option>
                            <option value="S2">S2</option>
                            <option value="S3">S3</option>
                            <option value="S4">S4</option>
                            <option value="S5">S5</option>
                            <option value="S6">S6</option>
                          </select>
                        </div>
                        <div className="edit-row">
                          <label>问题：</label>
                          <textarea
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            rows={2}
                          />
                        </div>
                        <div className="edit-row">
                          <label>来源：</label>
                          <input
                            type="text"
                            value={editReference}
                            onChange={(e) => setEditReference(e.target.value)}
                            placeholder="问题来源（可选）"
                          />
                        </div>
                        <div className="edit-actions">
                          <button onClick={saveEdit}>保存</button>
                          <button onClick={cancelEdit}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <div className="question-display">
                        <div className="question-content">
                          <span className="question-category">{question.category}</span>
                          <span className="question-text">{question.text}</span>
                          {question.reference && (
                            <span className="question-reference">来源: {question.reference}</span>
                          )}
                        </div>
                        <div className="question-actions">
                          <button onClick={() => startEdit(question)}>编辑</button>
                          <button onClick={() => deleteQuestion(question.id)} className="delete-btn">
                            删除
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default QuestionEdit;


