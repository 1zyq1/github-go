import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The bootstrap is a module side effect, so every case re-imports it with the
 * environment staged first: `vi.resetModules()` plus a fresh dynamic import is
 * the only way to exercise read-once logic more than once.
 */
async function boot({
  stored = null,
  systemDark,
  blocked = false,
}: {
  stored?: string | null;
  systemDark: boolean;
  blocked?: boolean;
}): Promise<void> {
  vi.resetModules();
  document.documentElement.classList.remove("dark");

  const entries = new Map<string, string>();
  if (stored !== null) {
    entries.set("theme", stored);
  }

  vi.stubGlobal("matchMedia", () => ({ matches: systemDark }));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => {
      if (blocked) {
        throw new Error("storage blocked");
      }
      return entries.get(key) ?? null;
    },
    setItem: (key: string, value: string) => {
      entries.set(key, value);
    },
  });

  await import("./theme-init");
}

describe("theme bootstrap", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.classList.remove("dark");
  });

  it("follows the system preference when nothing is stored", async () => {
    await boot({ systemDark: true });
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await boot({ systemDark: false });
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("lets a stored choice override the system preference", async () => {
    await boot({ stored: "dark", systemDark: false });
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await boot({ stored: "light", systemDark: true });
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("falls back to the system preference when storage is blocked", async () => {
    await boot({ stored: "dark", systemDark: true, blocked: true });
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
