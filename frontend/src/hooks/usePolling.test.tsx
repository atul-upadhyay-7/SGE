// @vitest-environment jsdom
import { StrictMode, type ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { usePolling } from "./usePolling";

const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

describe("polling lifecycle", () => {
  it("loads immediately after StrictMode's mount/abort/remount", async () => {
    const fn = vi.fn(async () => 42);
    const { result, unmount } = renderHook(() => usePolling(fn, 60_000), { wrapper });
    await waitFor(() => expect(result.current.data).toBe(42));
    expect(result.current.loading).toBe(false);
    expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2);
    unmount();
  });
  it("never allows a superseded device response to replace current values", async () => {
    let resolveOld: (v: string) => void = () => {};
    const old = new Promise<string>((resolve) => { resolveOld = resolve; });
    const { result, rerender, unmount } = renderHook(
      ({ device }) => usePolling(async () => device === "old" ? old : "new value", 60_000, [device]),
      { initialProps: { device: "old" } },
    );
    rerender({ device: "new" });
    await waitFor(() => expect(result.current.data).toBe("new value"));
    await act(async () => resolveOld("stale value"));
    expect(result.current.data).toBe("new value");
    unmount();
  });
  it("does not overlap manual refreshes with an in-flight request", async () => {
    let resolve: (v: number) => void = () => {};
    const fn = vi.fn(() => new Promise<number>((r) => { resolve = r; }));
    const { result, unmount } = renderHook(() => usePolling(fn, 60_000));
    act(() => { result.current.refresh(); result.current.refresh(); });
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => resolve(7));
    expect(result.current.data).toBe(7);
    unmount();
  });
});
