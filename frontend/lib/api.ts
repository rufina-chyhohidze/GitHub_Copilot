import type { Conversation, Page, Pending } from "./types";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new ApiError(
      "Connection lost. Retry when the repository service is available.",
      0,
    );
  }
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      body?.error?.message ||
        "The request could not be completed. Please retry.",
      response.status,
    );
  return body as T;
}
export const post = <T>(path: string, body: unknown = {}, headers = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body), headers });
export async function allPages<T>(path: string): Promise<T[]> {
  let offset: number | null = 0;
  const items: T[] = [];
  while (offset !== null) {
    const page: Page<T> = await api(`${path}?limit=100&offset=${offset}`);
    items.push(...page.items);
    offset = page.next_offset;
  }
  return items;
}
export async function loadConversation(id: string): Promise<Conversation> {
  const result = await api<Conversation>(`/conversations/${id}?limit=100`);
  let offset = result.next_offset;
  while (offset !== null) {
    const page = await api<Conversation>(
      `/conversations/${id}?limit=100&offset=${offset}`,
    );
    result.messages.push(...page.messages);
    result.runs.push(...page.runs);
    offset = page.next_offset;
  }
  return result;
}
export function store(key: string, value: unknown) {
  try {
    localStorage.setItem(`copilot:${key}`, JSON.stringify(value));
  } catch {
    /* Browsing works without storage. */
  }
}
export function recall<T>(key: string): T | null {
  try {
    return JSON.parse(
      localStorage.getItem(`copilot:${key}`) || "null",
    ) as T | null;
  } catch {
    return null;
  }
}
export function pendingRequest(id: string): Pending | null {
  return recall<Pending>(`pending:${id}`);
}
export const terminal = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);
export const errorText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please retry.";
