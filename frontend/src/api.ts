import { GeneratePayload, Category } from "./types";

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    let errorMsg = text || resp.statusText;
    try {
      const errorJson = JSON.parse(text);
      errorMsg = errorJson.detail || text;
    } catch {
      // If not JSON, use text as is
    }
    throw new Error(`请求失败 (${resp.status}): ${errorMsg}`);
  }
  return resp.json();
}

export async function fetchCategories(): Promise<Category[]> {
  const resp = await fetch("/api/categories");
  const data = await handle<{ categories: Category[] }>(resp);
  return data.categories;
}

export async function generateQuestions(payload: GeneratePayload) {
  const resp = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handle<{ request_id: string; questions: any[] }>(resp);
}

