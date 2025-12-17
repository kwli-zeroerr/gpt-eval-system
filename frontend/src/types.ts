export type Category = {
  id: string;
  title: string;
  description: string;
  default_prompt: string;
  default_count: number;
};

export type QuestionItem = {
  category: string;
  text: string;
  reference?: string;
};

export type GeneratePayload = {
  categories: string[];
  per_category: number;
  prompt_overrides: Record<string, string>;
  source_files?: string[];
};

