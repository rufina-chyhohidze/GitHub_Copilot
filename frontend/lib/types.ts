export type Page<T> = { items: T[]; next_offset: number | null };
export type Repository = {
  id: string;
  canonical_url: string;
  latest_ready_snapshot_id: string | null;
};
export type Snapshot = {
  id: string;
  repository_id: string;
  commit_sha: string;
  status: string;
  coverage: {
    stored_files?: number;
    total_files?: number;
    excluded_by_reason?: Record<string, number>;
    [key: string]: unknown;
  };
};
export type Job = {
  id: string;
  repository_id: string;
  snapshot_id: string | null;
  status: string;
  stage: string;
  progress: number;
  attempts?: number;
  lease_expires_at?: string | null;
  error: { code?: string; message?: string } | null;
};
export type Entry = {
  path: string;
  type: "directory" | "file";
  status?: string;
  reason?: string;
};
export type Tree = { entries: Entry[]; next_offset: number | null };
export type Source = {
  snapshot_id: string;
  path: string;
  content: string;
  start_line: number | null;
  end_line: number | null;
  truncated: boolean;
};
export type Citation = {
  id: string;
  url: string;
  source: {
    snapshot_id: string;
    path: string;
    start_line: number;
    end_line: number;
  };
};
export type Answer = {
  claims: { text: string; evidence_ids: string[] }[];
  uncertainty: string | null;
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: {
    run_id?: string;
    text?: string;
    answer?: Answer;
    citations?: Citation[];
    validated?: boolean;
  };
};
export type Run = {
  id: string;
  status: string;
  input_message_id: string;
  error: string | null;
};
export type Conversation = {
  id: string;
  title: string;
  snapshot_id: string;
  messages: Message[];
  runs: Run[];
  next_offset: number | null;
};
export type Selection = {
  snapshot: string;
  path: string;
  start: number;
  end?: number;
};
export type Pending = {
  conversation: string;
  key: string;
  question: string;
  pipeline: "fixed" | "agent";
};
