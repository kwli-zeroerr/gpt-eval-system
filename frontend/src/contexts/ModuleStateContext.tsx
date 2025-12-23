import { createContext, useContext, useState, ReactNode, useCallback } from "react";

interface ModuleState {
  [moduleId: string]: any;
}

interface ModuleStateContextType {
  moduleStates: ModuleState;
  setModuleState: (moduleId: string, state: any) => void;
  getModuleState: (moduleId: string) => any;
}

const ModuleStateContext = createContext<ModuleStateContextType | undefined>(undefined);

export function ModuleStateProvider({ children }: { children: ReactNode }) {
  const [moduleStates, setModuleStates] = useState<ModuleState>(() => {
    // 从 localStorage 恢复状态
    try {
      const saved = localStorage.getItem("moduleStates");
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });

  const setModuleState = useCallback((moduleId: string, state: any) => {
    setModuleStates((prev) => {
      const newStates = { ...prev, [moduleId]: state };
      // 保存到 localStorage
      try {
        localStorage.setItem("moduleStates", JSON.stringify(newStates));
      } catch {
        // 忽略存储错误
      }
      return newStates;
    });
  }, []);

  const getModuleState = useCallback((moduleId: string) => {
    return moduleStates[moduleId] || null;
  }, [moduleStates]);

  return (
    <ModuleStateContext.Provider value={{ moduleStates, setModuleState, getModuleState }}>
      {children}
    </ModuleStateContext.Provider>
  );
}

export function useModuleState() {
  const context = useContext(ModuleStateContext);
  if (!context) {
    throw new Error("useModuleState must be used within ModuleStateProvider");
  }
  return context;
}

