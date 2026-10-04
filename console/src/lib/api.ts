export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

export const SIGNED_OUT_EVENT = "intentlatch:signed-out";
const SESSION_PATH = "/console/session";

type ErrorBody = { error?: { code?: string; message?: string } };

export async function api<T>(path: string, { method = "GET", body }: { method?: string; body?: unknown } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      // Required by the gateway on every console write; see gateway auth.py.
      headers: { "X-IntentLatch-Console": "1", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "gateway_unreachable", "The gateway cannot be reached. Check that it is running.");
  }
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (response.ok) return data as T;

  const error = (data as ErrorBody | null)?.error;
  if (!error?.code) {
    throw new ApiError(response.status, "gateway_unreachable", `The gateway did not answer (HTTP ${response.status}).`);
  }
  if (response.status === 401 && path !== SESSION_PATH) window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  throw new ApiError(response.status, error.code, error.message ?? "The request failed.");
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
