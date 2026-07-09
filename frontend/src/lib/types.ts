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
  status: "draft" | "in_review" | "pre_approval" | "completed";
  word_count: number | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  content?: string | null;
  sections?: ReportSection[];
  // May contain a "citation_confidence" key (see backend services/citation_verifier.py)
  // alongside any other keys already stored in this JSON blob.
  metadata_json?: string | null;
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
  citation_confidence_json: string | null;
  sources_json: string | null;
  session_id: string | null;
  created_at: string;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

// Result of backend services/citation_verifier.py's verify_grounding() call.
export interface CitationConfidence {
  confidence_score?: number;
  unsupported_claims?: string[];
  notes?: string;
}

// Sentence-level numbered citation emitted by the Ask Documents RAG chat
// (backend services/rag_service.py answer_question's "complete" SSE event).
// `index` is the 1-based number Claude cites inline as [N] in the answer text.
export interface Citation {
  index: number;
  doc_id: string;
  chunk_id: string;
  page: number;
  title: string;
  snippet: string;
}

// Web-search citation emitted by the Ask Documents RAG chat when Claude's
// answer draws on the server-side web_search tool (backend
// services/anthropic_client.py's extract_web_citations, surfaced via
// rag_service.py answer_question's "complete" SSE event). Unlike Citation
// (library sources), these are not numbered / referenced by [N] markers.
export interface WebCitation {
  url: string;
  title: string;
  cited_text: string;
}

// Block-level chat message content, replayed across Ask Documents turns so
// prior tool_use/tool_result blocks survive (see backend
// services/anthropic_client.py's serialize_content_blocks).
export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface ApiChatMessage {
  role: "user" | "assistant";
  content: string | ContentBlock[];
}

export interface Debate {
  id: string;
  topic: string;
  status: "pending" | "running" | "complete" | "error";
  personas: string | null;
  synthesis: string | null;
  // JSON-encoded { claims: ConsensusClaim[] } result of backend
  // services/consensus_meter.py's extract_consensus() call (see below).
  consensus_json: string | null;
  created_at: string;
  completed_at: string | null;
}

// One claim in the Multi-Persona Debate's "Consensus Meter" — see backend
// services/consensus_meter.py's extract_consensus(). `stances` is keyed by
// persona_key (see backend templates/personas.py), one entry per persona
// who participated in the debate.
export interface ConsensusClaim {
  claim: string;
  stances: Record<string, "agree" | "disagree" | "mixed">;
}
