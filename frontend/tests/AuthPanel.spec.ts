import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthPanel from "../src/components/AuthPanel.vue";

// Every wrapper is unmounted after its test, which is what lets the pending view
// drop its `visibilitychange` listener instead of leaving it on the document for
// a later test to trip over.
enableAutoUnmount(afterEach);

const fetchMock = vi.fn<(input: string, init: RequestInit) => Promise<unknown>>();

function jsonResponse(body: unknown, status = 200): unknown {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

function errorBody(code: string, message: string): unknown {
  return { error: { code, message } };
}

function sessionBody(): unknown {
  return {
    authenticated: true,
    username: "alice",
    quota: { limit: 10, remaining: 9, reset_after_seconds: 3599 },
    email: "alice@example.com",
    email_verified: true,
  };
}

function pendingBody(email: string | null): unknown {
  return {
    authenticated: false,
    username: "alice",
    quota: null,
    email,
    email_verified: false,
  };
}

function lastCall(): readonly [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (call === undefined) {
    throw new Error("expected the panel to call the API");
  }
  return call;
}

/** The number of auth calls, ignoring the SSO provider probe made on mount. */
function authCalls(): number {
  return fetchMock.mock.calls.filter(([input]) => input !== "/api/sso/providers").length;
}

async function submit(
  view: VueWrapper,
  username: string,
  password: string,
  email = "",
): Promise<void> {
  await view.get("input#auth-username").setValue(username);
  await view.get("input#auth-password").setValue(password);
  const address = view.find("input#auth-email");
  if (address.exists()) {
    await address.setValue(email);
  }
  await view.get("form").trigger("submit");
  await flushPromises();
}

async function useRegisterMode(view: VueWrapper): Promise<void> {
  await view.get("input[type='radio'][value='register']").setValue(true);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthPanel", () => {
  it("starts in login mode and asks for an existing password", () => {
    const view = mount(AuthPanel);

    expect(view.get("h2").text()).toBe("登录后使用加速服务");
    expect(view.get("button[type='submit']").text()).toBe("登录");
    expect(view.get("input#auth-username").attributes("autocomplete")).toBe("username");
    expect(view.get("input#auth-password").attributes("type")).toBe("password");
    expect(view.get("input#auth-password").attributes("autocomplete")).toBe("current-password");
    expect(view.get<HTMLInputElement>("input[type='radio'][value='login']").element.checked).toBe(
      true,
    );
    expect(authCalls()).toBe(0);
  });

  it("states the hourly allowance in the lead copy", () => {
    const view = mount(AuthPanel);

    expect(view.get(".auth__lead").text()).toContain("每小时可发起 10 次");
  });

  it("switches to registration and asks for a new password", async () => {
    const view = mount(AuthPanel);

    await useRegisterMode(view);

    expect(view.get("h2").text()).toBe("注册新账号");
    expect(view.get("button[type='submit']").text()).toBe("注册");
    expect(view.get("input#auth-password").attributes("autocomplete")).toBe("new-password");
  });

  it("rejects an empty username before spending a request", async () => {
    const view = mount(AuthPanel);

    await submit(view, "   ", "secret123");

    expect(view.get("[data-test='username-error']").text()).toBe("请填写用户名。");
    expect(authCalls()).toBe(0);
  });

  it("rejects a username that breaks the registration shape", async () => {
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "ab", "12345678");

    expect(view.get("[data-test='username-error']").text()).toContain("3 至 32 位");
    expect(authCalls()).toBe(0);
  });

  it("rejects a short registration password", async () => {
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "1234567");

    expect(view.get("[data-test='password-error']").text()).toBe("密码长度需为 8 至 128 位。");
    expect(authCalls()).toBe(0);
  });

  it("marks only the offending field as invalid", async () => {
    const view = mount(AuthPanel);

    await submit(view, "", "");

    expect(view.get("input#auth-username").attributes("aria-invalid")).toBe("true");
    expect(view.get("input#auth-password").attributes("aria-invalid")).toBe("false");
  });

  it("moves focus to the field that needs fixing", async () => {
    const view = mount(AuthPanel, { attachTo: document.body });

    await submit(view, "alice", "");

    expect(document.activeElement).toBe(view.get("input#auth-password").element);
    view.unmount();
  });

  it("clears a stale error when the mode changes", async () => {
    const view = mount(AuthPanel);

    await submit(view, "", "secret123");
    expect(view.get("[data-test='username-error']").text()).toBe("请填写用户名。");

    await useRegisterMode(view);

    expect(view.get("[data-test='username-error']").text()).toBe("");
  });

  it("lets an existing account sign in with a short password", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    const view = mount(AuthPanel);

    await submit(view, "alice", "x");

    expect(lastCall()[0]).toBe("/api/auth/login");
  });

  it("trims the username, signs in, clears the password and reports success", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    const view = mount(AuthPanel);

    await submit(view, "  alice  ", "secret123");

    expect(JSON.parse(String(lastCall()[1].body))).toEqual({
      username: "alice",
      password: "secret123",
    });
    expect(view.emitted("authenticated")).toHaveLength(1);
    expect(view.get<HTMLInputElement>("input#auth-password").element.value).toBe("");
  });

  it("registers a new account in registration mode", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "secret123", "alice@example.com");

    expect(lastCall()[0]).toBe("/api/auth/register");
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({
      username: "alice",
      password: "secret123",
      email: "alice@example.com",
    });
    expect(view.emitted("authenticated")).toHaveLength(1);
  });

  it("places a taken username on the username field", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("username_taken", "该用户名已被占用。"), 409),
    );
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "secret123", "alice@example.com");

    expect(view.get("[data-test='username-error']").text()).toBe("该用户名已被占用。");
    expect(view.get("[data-test='form-error']").text()).toBe("");
    expect(view.emitted("authenticated")).toBeUndefined();
  });

  it("places a taken address on the address field", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("email_taken", "该邮箱已被其他账号使用。"), 409),
    );
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "secret123", "taken@example.com");

    expect(view.get("[data-test='email-error']").text()).toBe("该邮箱已被其他账号使用。");
    expect(view.get("[data-test='form-error']").text()).toBe("");
  });

  it("only asks for an address while registering", async () => {
    const view = mount(AuthPanel);

    expect(view.find("input#auth-email").exists()).toBe(false);

    await useRegisterMode(view);

    expect(view.find("input#auth-email").exists()).toBe(true);
  });

  it("rejects an unusable address before spending a request", async () => {
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "12345678", "nobody@example");

    expect(view.get("[data-test='email-error']").text()).toContain("有效的邮箱地址");
    expect(authCalls()).toBe(0);
  });

  it("waits for the mailed link after registering", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);
    await useRegisterMode(view);

    await submit(view, "alice", "secret123", "alice@example.com");

    expect(view.get("h2").text()).toBe("验证邮箱");
    expect(view.get("[data-test='pending-notice']").text()).toBe("验证邮件已发送。");
    expect(view.get(".auth__lead").text()).toContain("alice@example.com");
    expect(view.get<HTMLInputElement>("input#auth-email").element.value).toBe("alice@example.com");
    expect(view.emitted("authenticated")).toBeUndefined();
  });

  it("mails a fresh link when the pending account asks again", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");

    expect(view.get("h2").text()).toBe("验证邮箱");

    await view.get("form").trigger("submit");
    await flushPromises();

    expect(lastCall()[0]).toBe("/api/auth/verify/request");
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({
      username: "alice",
      password: "secret123",
      email: "alice@example.com",
    });
    expect(view.get("[data-test='pending-notice']").text()).toBe("验证邮件已重新发送。");
  });

  it("wants an address when the pending account has none", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody(null)));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");

    expect(view.get(".auth__lead").text()).toContain("还没有邮箱地址");
    expect(view.get<HTMLInputElement>("input#auth-email").element.value).toBe("");

    await view.get("form").trigger("submit");
    await flushPromises();

    expect(view.get("[data-test='email-error']").text()).toBe("请填写邮箱地址。");
    expect(authCalls()).toBe(1);
  });

  it("returns to the login form from the pending view", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    await view.get("button[type='button']").trigger("click");
    await flushPromises();

    expect(view.get("h2").text()).toBe("登录后使用加速服务");
    expect(view.find("input#auth-email").exists()).toBe(false);
    expect(authCalls()).toBe(1);
  });

  it("adopts the confirmation as soon as the visitor comes back", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    expect(view.get("h2").text()).toBe("验证邮箱");

    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();

    expect(lastCall()[0]).toBe("/api/auth/me");
    expect(view.emitted("authenticated")).toHaveLength(1);
  });

  it("keeps waiting while the address is still unconfirmed", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();

    expect(lastCall()[0]).toBe("/api/auth/me");
    expect(view.emitted("authenticated")).toBeUndefined();
    expect(view.get("h2").text()).toBe("验证邮箱");
  });

  it("survives a probe that fails while the visitor is away", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();

    expect(view.get("h2").text()).toBe("验证邮箱");
    expect(view.get("[data-test='form-error']").text()).toBe("");
  });

  it("stops listening once the visitor returns to the form", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    await view.get("button[type='button']").trigger("click");
    await flushPromises();
    const calls = fetchMock.mock.calls.length;

    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(calls);
    expect(view.emitted("authenticated")).toBeUndefined();
  });

  it("also notices the confirmation when the window regains focus", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    window.dispatchEvent(new Event("focus"));
    await flushPromises();

    expect(lastCall()[0]).toBe("/api/auth/me");
    expect(view.emitted("authenticated")).toHaveLength(1);
  });

  it("shares one probe between events that arrive together", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();

    // The registration plus a single probe, not one probe per event.
    expect(authCalls()).toBe(2);
    expect(view.emitted("authenticated")).toHaveLength(1);
  });

  it("reports wrong credentials at form level", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("invalid_credentials", "用户名或密码不正确。"), 401),
    );
    const view = mount(AuthPanel);

    await submit(view, "alice", "wrong");

    expect(view.get("[data-test='form-error']").text()).toBe("用户名或密码不正确。");
    expect(view.get("[data-test='username-error']").text()).toBe("");
  });

  it("reports a network failure and re-enables submission", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = mount(AuthPanel);

    await submit(view, "alice", "secret123");

    expect(view.get("[data-test='form-error']").text()).toBe("网络请求失败，请检查连接后重试。");
    expect(view.get("button[type='submit']").text()).toBe("登录");
    expect(view.get("button[type='submit']").attributes("disabled")).toBeUndefined();
  });

  it("ignores a second submit while the first is in flight", async () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    const view = mount(AuthPanel);

    await view.get("input#auth-username").setValue("alice");
    await view.get("input#auth-password").setValue("secret123");
    await view.get("form").trigger("submit");
    await view.get("form").trigger("submit");
    await flushPromises();

    expect(authCalls()).toBe(1);
    expect(view.get("button[type='submit']").text()).toBe("正在登录");
    expect(view.get("button[type='submit']").attributes("disabled")).toBeDefined();
  });

  it("offers the blog sign-in when SSO is configured", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ blog: true }));
    const view = mount(AuthPanel);
    await flushPromises();

    expect(view.get("a.auth__sso-button").attributes("href")).toBe("/api/sso/start");
  });

  it("hides the blog sign-in when SSO is not configured", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ blog: false }));
    const view = mount(AuthPanel);
    await flushPromises();

    expect(view.find("a.auth__sso-button").exists()).toBe(false);
  });
});
