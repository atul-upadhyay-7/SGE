// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, ApiError } from "./api";
import { setOperatorKey } from "./operatorKey";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  setOperatorKey("");
  vi.unstubAllGlobals();
});

describe("operator key", () => {
  it("is sent on writes only, and never on reads", async () => {
    setOperatorKey("secret-op-key");
    const fn = mockFetch(200, {});
    await apiFetch("/reports", { method: "POST", body: {} });
    await apiFetch("/alerts");
    const [, post] = fn.mock.calls[0] as unknown as [string, RequestInit];
    const [, get] = fn.mock.calls[1] as unknown as [string, RequestInit];
    expect((post.headers as Record<string, string>)["X-Operator-Key"]).toBe("secret-op-key");
    expect((get.headers as Record<string, string>)["X-Operator-Key"]).toBeUndefined();
  });

  it("explains a 401 without echoing server detail", async () => {
    mockFetch(401, { detail: "missing or invalid 'X-Operator-Key' header" });
    const error = await apiFetch("/reports", { method: "POST", body: {} }).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toMatch(/needs the operator key/i);
  });
});
