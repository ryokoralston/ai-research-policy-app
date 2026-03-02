export interface ResearchSession {
  id: string;
  query: string;
  topic: string | null;
  status: "pending" | "running" | "complete" | "error";
  summary: string | null;
  created_at: string;
  completed_at: string | null;
  results?: SearchResult[];
}

export interface SearchResult {
  id: string;
  url: string;
  title: string | null;
  snippet: string | null;
  ai_summary: string | null;
  relevance_score: number | null;
  published_date: string | null;
  result_order: number;
}

export interface Document {
  id: string;
  filename: string;
  title: string | null;
  source_type: string;
  url: string | null;
  page_count: number | null;
  word_count: number | null;
  status: "processing" | "indexed" | "error";
  created_at: string;
  indexed_at: string | null;
  chunk_count: number;
  file_path?: string | null;
  metadata_json: string | null;
}

export interface Report {
  id: string;
  title: string;
  report_type: "congressional_brief" | "policy_memo" | "risk_assessment";
  status: "draft" | "complete" | "archived";
  word_count: number | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  content?: string | null;
  sections?: ReportSection[];
}

export interface ReportSection {
  id: string;
  section_key: string;
  title: string;
  content: string;
  order_index: number;
  citations_json: string | null;
}

export interface RiskAnalysis {
  id: string;
  subject: string;
  analysis_type: string;
  content: string | null;
  risk_scores_json: string | null;
  sources_json: string | null;
  session_id: string | null;
  created_at: string;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}
