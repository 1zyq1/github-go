import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.vue";

const fetchMock = vi.fn<(input: string, init: RequestInit) => Promise<unknown>>();

function jsonResponse(body: unknown, status = 200): unknown {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

function anonymousBody(): unknown {
  return { authenticated: false, username: null, quota: null };
}

function sessionBody(remaining: number, resetAfterSeconds = 1800): unknown {
  return {
    authenticated: true,
    username: "alice",
    quota: { limit: 10, remaining, reset_after_seconds: resetAfterSeconds },
  };
}

function statsBody(): unknown {
  return {
    windows: [
      { key: "hour", downloads: 3, pages: 1, users: 2 },
      { key: "day", downloads: 9, pages: 4, users: 5 },
      { key: "week", downloads: 20, pages: 8, users: 11 },
      { key: "month", downloads: 44, pages: 16, users: 23 },
      { key: "total", downloads: 90, pages: 31, users: 40 },
    ],
  };
}

/**
 * Answer `/api/auth/me` from a queue, holding the last entry once it is reached.
 * Every other path succeeds with the anonymous session, except the public
 * report, which always answers with a fixed tally.
 */
function serveSessions(first: unknown, ...rest: readonly unknown[]): void {
  const queue = [first, ...rest];
  let index = 0;
  fetchMock.mockImplementation((path) => {
    if (path === "/api/auth/me") {
      const body = queue[Math.min(index, queue.length - 1)] ?? anonymousBody();
      index += 1;
      return Promise.resolve(jsonResponse(body));
    }
    if (path === "/api/stats") {
      return Promise.resolve(jsonResponse(statsBody()));
    }
    return Promise.resolve(jsonResponse(anonymousBody()));
  });
}

async function renderApp(): Promise<VueWrapper> {
  const view = mount(App);
  await flushPromises();
  return view;
}

async function submitConverter(view: VueWrapper, value: string): Promise<void> {
  await view.get("input#source-url").setValue(value);
  await view.get("form.converter__form").trigger("submit");
  await flushPromises();
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("App", () => {
  it("renders a single main landmark", async () => {
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.findAll("main#main")).toHaveLength(1);
  });

  it("reports that it is checking the session before answering", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    const view = mount(App);

    expect(view.get(".page__status").text()).toBe("正在检查登录状态。");
    expect(view.find(".converter").exists()).toBe(false);
    view.unmount();
  });

  it("asks an anonymous visitor to sign in", async () => {
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.find("section.auth").exists()).toBe(true);
    expect(view.find(".converter").exists()).toBe(false);
    expect(view.find("[data-test='session-chip']").exists()).toBe(false);
  });

  it("shows the account and its allowance to a signed-in visitor", async () => {
    serveSessions(sessionBody(7));
    const view = await renderApp();

    expect(view.get("[data-test='session-user']").text()).toBe("alice");
    expect(view.get("[data-test='session-quota']").text()).toBe("剩余 7 / 10 次");
    expect(view.find(".converter").exists()).toBe(true);
    expect(view.find("section.auth").exists()).toBe(false);
    expect(view.get("button[type='submit']").attributes("disabled")).toBeUndefined();
  });

  it("keeps the reset time out of the way while allowance remains", async () => {
    serveSessions(sessionBody(8, 900));
    const view = await renderApp();

    expect(view.find("[data-test='session-reset']").exists()).toBe(false);
  });

  it("ignores a reset time the API reports while units remain", async () => {
    // The API only fills reset_after_seconds once a unit is blocking, so a stray
    // value must never turn into a countdown.
    serveSessions(sessionBody(1, 900));
    const view = await renderApp();

    expect(view.find("[data-test='session-reset']").exists()).toBe(false);
  });

  it("warns about the reset time once the allowance is spent", async () => {
    serveSessions(sessionBody(0, 90));
    const view = await renderApp();

    expect(view.get("[data-test='session-reset']").text()).toBe("额度将在 2 分钟后重置");
  });

  it("blocks the converter and explains once the allowance is spent", async () => {
    serveSessions(sessionBody(0, 60));
    const view = await renderApp();

    expect(view.get("[data-test='session-quota']").text()).toBe("剩余 0 / 10 次");
    expect(view.get("[data-test='quota-notice']").text()).toContain("本小时的访问额度已用完");
    expect(view.get("button[type='submit']").attributes("disabled")).toBeDefined();
  });

  it("offers a blog-credit redeem when the allowance is spent and SSO is on", async () => {
    fetchMock.mockImplementation((path) => {
      if (path === "/api/auth/me") {
        return Promise.resolve(jsonResponse(sessionBody(0, 60)));
      }
      if (path === "/api/sso/providers") {
        return Promise.resolve(
          jsonResponse({
            blog: true,
            redeem_enabled: true,
            redeem_credits: 10,
            redeem_attempts: 10,
          }),
        );
      }
      return Promise.resolve(jsonResponse(anonymousBody()));
    });
    const view = await renderApp();

    const panel = view.get("[data-test='redeem-panel']");
    expect(panel.text()).toContain("10 积分");
    expect(view.get("[data-test='redeem-button']").text()).toBe("用博客积分兑换");
  });

  it("redeems blog credits and restores the allowance", async () => {
    fetchMock.mockImplementation((path) => {
      if (path === "/api/auth/me") {
        return Promise.resolve(jsonResponse(sessionBody(0, 60)));
      }
      if (path === "/api/sso/providers") {
        return Promise.resolve(
          jsonResponse({
            blog: true,
            redeem_enabled: true,
            redeem_credits: 10,
            redeem_attempts: 10,
          }),
        );
      }
      if (path === "/api/redeem") {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            credits_spent: 10,
            attempts_granted: 10,
            bonus: 10,
            quota: { limit: 10, remaining: 10, reset_after_seconds: 60 },
          }),
        );
      }
      return Promise.resolve(jsonResponse(anonymousBody()));
    });
    const view = await renderApp();

    await view.get("[data-test='redeem-button']").trigger("click");
    await flushPromises();

    expect(view.get("[data-test='redeem-notice']").text()).toContain("已用 10 积分兑换 10 次");
    // The fresh quota re-enables the converter without a reload.
    expect(view.find("[data-test='redeem-panel']").exists()).toBe(false);
  });

  it("explains a refused download even when the session cannot be re-read", async () => {
    window.history.replaceState({}, "", "/?limited=1");
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = await renderApp();

    expect(view.find("[data-test='quota-notice']").exists()).toBe(true);
  });

  it("drops the redirect notice once the allowance is available again", async () => {
    window.history.replaceState({}, "", "/?limited=1");
    serveSessions(sessionBody(5));
    const view = await renderApp();

    expect(view.find("[data-test='quota-notice']").exists()).toBe(false);
  });

  it("congratulates the visitor whose address was just confirmed", async () => {
    window.history.replaceState({}, "", "/?verified=1");
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.get("[data-test='verified-notice']").text()).toContain("邮箱验证成功");
  });

  it("explains a verification link that no longer works", async () => {
    window.history.replaceState({}, "", "/?verify_failed=1");
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.get("[data-test='verify-failed']").text()).toContain("无效或已过期");
  });

  it("says nothing about verification on an ordinary visit", async () => {
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.find("[data-test='verified-notice']").exists()).toBe(false);
    expect(view.find("[data-test='verify-failed']").exists()).toBe(false);
  });

  it("keeps the sign-in form up while an address waits for confirmation", async () => {
    serveSessions(anonymousBody());
    const view = await renderApp();

    await view.get("input[type='radio'][value='register']").setValue(true);
    await view.get("input#auth-username").setValue("alice");
    await view.get("input#auth-password").setValue("secret123");
    await view.get("input#auth-email").setValue("alice@example.com");
    await view.get("form").trigger("submit");
    await flushPromises();

    expect(view.get("h2").text()).toBe("验证邮箱");
    expect(view.get(".auth__lead").text()).toContain("alice@example.com");
    expect(view.find("section.converter").exists()).toBe(false);
  });

  it("asks the visitor to reload when the session cannot be read", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = await renderApp();

    expect(view.get(".notice--warning").text()).toContain("无法确认登录状态");
    expect(view.find("section.auth").exists()).toBe(true);
  });

  it("signs out and returns to the sign-in form", async () => {
    serveSessions(sessionBody(6));
    const view = await renderApp();

    await view.get("[data-test='session-chip'] button").trigger("click");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
    expect(view.find("section.auth").exists()).toBe(true);
    expect(view.find("[data-test='session-chip']").exists()).toBe(false);
  });

  it("returns to the sign-in form and explains when the session lapses mid-use", async () => {
    let sessionReads = 0;
    fetchMock.mockImplementation((path) => {
      if (path === "/api/auth/me") {
        sessionReads += 1;
        const body = sessionReads === 1 ? sessionBody(10) : anonymousBody();
        return Promise.resolve(jsonResponse(body));
      }
      if (path === "/api/resolve") {
        return Promise.resolve(
          jsonResponse({ error: { code: "unauthorized", message: "请先登录后再使用。" } }, 401),
        );
      }
      return Promise.resolve(jsonResponse(anonymousBody()));
    });
    const view = await renderApp();

    await submitConverter(view, "acme/widgets");

    expect(view.get(".notice--warning").text()).toContain("登录状态已失效");
    expect(view.find("section.auth").exists()).toBe(true);
  });
});

describe("App on the data page", () => {
  function openDataPage(): void {
    window.history.replaceState({}, "", "/stats");
  }

  it("renders the report instead of the sign-in flow", async () => {
    openDataPage();
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.find("section.stats").exists()).toBe(true);
    expect(view.find(".converter").exists()).toBe(false);
    expect(view.find("section.auth").exists()).toBe(false);
  });

  it("reads the report from anyone without asking them to sign in", async () => {
    openDataPage();
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/stats",
      expect.objectContaining({ method: "GET" }),
    );
    expect(view.get("[data-test='stats-total'] [data-test='stats-downloads']").text()).toBe("90");
  });

  it("keeps the shared header and marks the entry that is open", async () => {
    openDataPage();
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.get("h1").text()).toBe("GitHub 文件加速");
    expect(view.findAll("nav a")).toHaveLength(3);
    expect(view.get("a[aria-current='page']").text()).toBe("数据");
  });

  it("marks the accelerator entry on the landing page", async () => {
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.get("a[aria-current='page']").text()).toBe("加速");
  });

  it("still shows the account chip and its allowance", async () => {
    openDataPage();
    serveSessions(sessionBody(7));
    const view = await renderApp();

    expect(view.get("[data-test='session-user']").text()).toBe("alice");
    expect(view.get("[data-test='session-quota']").text()).toBe("剩余 7 / 10 次");
  });

  it("leaves the feature band to the landing page", async () => {
    openDataPage();
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.find("section.feature-strip").exists()).toBe(false);
  });

  it("keeps the quota notice off a page that spends nothing", async () => {
    window.history.replaceState({}, "", "/stats?limited=1");
    serveSessions(sessionBody(0, 60));
    const view = await renderApp();

    expect(view.find("[data-test='quota-notice']").exists()).toBe(false);
    expect(view.find("section.stats").exists()).toBe(true);
  });

  it("accepts the trailing slash form of the address", async () => {
    window.history.replaceState({}, "", "/stats/");
    serveSessions(anonymousBody());
    const view = await renderApp();

    expect(view.find("section.stats").exists()).toBe(true);
  });
});
