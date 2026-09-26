import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AuthError,
  type AuthMode,
  credentialProblem,
  emailProblem,
  fetchSession,
  fetchStats,
  login,
  logout,
  register,
  requestVerification,
} from "../src/api/client";

const fetchMock = vi.fn<(input: string, init: RequestInit) => Promise<unknown>>();

function jsonResponse(body: unknown, status = 200): unknown {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  };
}

function errorBody(code: string, message: string): unknown {
  return { error: { code, message } };
}

function sessionBody(): unknown {
  return {
    authenticated: true,
    username: "alice",
    quota: { limit: 10, remaining: 7, reset_after_seconds: 1200 },
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

function firstCall(): readonly [string, RequestInit] {
  const call = fetchMock.mock.calls[0];
  if (call === undefined) {
    throw new Error("expected fetch to have been called");
  }
  return call;
}

function firstBody(): unknown {
  const [, init] = firstCall();
  return JSON.parse(String(init.body));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("credentialProblem", () => {
  const accepted: readonly (readonly [AuthMode, string, string, string])[] = [
    ["login", "alice", "secret", ""],
    ["login", "alice", "x", ""],
    ["login", "a b", "secret", ""],
    ["login", "a".repeat(32), "secret", ""],
    ["login", "alice", "secret", "not-an-address"],
    ["register", "abc", "12345678", "abc@example.com"],
    ["register", "a-b_c", "12345678", "a-b_c@mail.example.org"],
    ["register", "1zyq1", "z".repeat(128), "user@example.com"],
    ["register", "a".repeat(32), "12345678", "long@sub.domain.example"],
    ["register", "abc", "12345678", `${"a".repeat(64)}@example.com`],
  ];

  for (const [mode, username, password, email] of accepted) {
    it(`accepts ${mode} with a ${username.length} char username, ${password.length} char password and ${email.length} char address`, () => {
      expect(credentialProblem(mode, username, password, email)).toBeNull();
    });
  }

  const rejected: readonly (readonly [
    AuthMode,
    string,
    string,
    string,
    "username" | "password" | "email",
  ])[] = [
    ["login", "", "secret", "", "username"],
    ["register", "", "12345678", "abc@example.com", "username"],
    ["login", "a".repeat(33), "secret", "", "username"],
    ["register", "a".repeat(33), "12345678", "abc@example.com", "username"],
    ["register", "ab", "12345678", "abc@example.com", "username"],
    ["register", "-abc", "12345678", "abc@example.com", "username"],
    ["register", "abc-", "12345678", "abc@example.com", "username"],
    ["register", "ab.c", "12345678", "abc@example.com", "username"],
    ["register", "ab cd", "12345678", "abc@example.com", "username"],
    ["login", "alice", "", "", "password"],
    ["register", "alice", "", "abc@example.com", "password"],
    ["register", "alice", "1234567", "abc@example.com", "password"],
    ["register", "alice", "z".repeat(129), "abc@example.com", "password"],
    ["register", "alice", "12345678", "", "email"],
    ["register", "alice", "12345678", "nowhere", "email"],
    ["register", "alice", "12345678", "nobody@example", "email"],
    ["register", "alice", "12345678", "two@@example.com", "email"],
    ["register", "alice", "12345678", "a b@example.com", "email"],
    ["register", "alice", "12345678", `${"a".repeat(64)}@${"b".repeat(190)}.com`, "email"],
  ];

  for (const [mode, username, password, email, field] of rejected) {
    it(`rejects ${mode} with a ${username.length} char username, ${password.length} char password and ${email.length} char address on the ${field}`, () => {
      expect(credentialProblem(mode, username, password, email)?.field).toBe(field);
    });
  }

  it("explains the username shape", () => {
    expect(credentialProblem("register", "ab", "12345678", "abc@example.com")?.message).toBe(
      "用户名需为 3 至 32 位字母、数字、下划线或连字符，并以字母或数字开头和结尾。",
    );
  });

  it("demands a password before anything else about it", () => {
    expect(credentialProblem("register", "alice", "", "abc@example.com")?.message).toBe(
      "请填写密码。",
    );
  });

  it("explains the address shape", () => {
    expect(credentialProblem("register", "alice", "12345678", "abc@example")?.message).toBe(
      "请填写有效的邮箱地址，例如 name@example.com。",
    );
  });

  it("never asks a signing-in account for an address", () => {
    expect(credentialProblem("login", "alice", "secret", "")).toBeNull();
    expect(credentialProblem("login", "alice", "secret", "broken@@address")).toBeNull();
  });

  it("leaves the trimmed-username contract to the caller", () => {
    expect(credentialProblem("login", "   ", "secret", "")).toBeNull();
  });
});

describe("emailProblem", () => {
  it("accepts an address that can receive mail", () => {
    expect(emailProblem("first.last+tag@sub.example.org")).toBeNull();
    expect(emailProblem(`${"a".repeat(64)}@example.com`)).toBeNull();
  });

  it("wants an address at all", () => {
    expect(emailProblem("")?.message).toBe("请填写邮箱地址。");
  });

  it("rejects shapes that could never receive mail", () => {
    const shapes: readonly string[] = [
      "nowhere",
      "nobody@example",
      "@example.com",
      "a@",
      "a b@example.com",
      "a@b@c.com",
      `${"a".repeat(65)}@example.com`,
      `${"a".repeat(64)}@${"b".repeat(190)}.com`,
    ];

    for (const address of shapes) {
      expect(emailProblem(address)?.message).toBe("请填写有效的邮箱地址，例如 name@example.com。");
    }
  });
});

describe("fetchSession", () => {
  it("reads the account and its allowance", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));

    await expect(fetchSession()).resolves.toEqual({
      authenticated: true,
      username: "alice",
      quota: { limit: 10, remaining: 7, resetAfterSeconds: 1200 },
      email: "alice@example.com",
      emailVerified: true,
    });
  });

  it("asks the me endpoint for JSON with the session cookie attached", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));

    await fetchSession();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/me",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
  });

  it("reports an anonymous visitor without throwing", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ authenticated: false, username: null, quota: null }),
    );

    await expect(fetchSession()).resolves.toEqual({
      authenticated: false,
      username: null,
      quota: null,
      email: null,
      emailVerified: null,
    });
  });

  it("rejects a session body that is not shaped like one", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ authenticated: "yes" }));

    await expect(fetchSession()).rejects.toMatchObject({
      code: "invalid_response",
      status: 200,
    });
  });

  it("rejects a signed-in session without a username", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ authenticated: true, username: null }));

    await expect(fetchSession()).rejects.toBeInstanceOf(AuthError);
  });

  it("treats a missing quota as no quota", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ authenticated: false, quota: { limit: 10 } }));

    await expect(fetchSession()).resolves.toEqual({
      authenticated: false,
      username: null,
      quota: null,
      email: null,
      emailVerified: null,
    });
  });

  it("falls back to a generic code when the envelope is missing", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 500));

    await expect(fetchSession()).rejects.toMatchObject({
      code: "request_failed",
      message: "操作失败，请稍后重试。",
      status: 500,
    });
  });
});

describe("register", () => {
  it("posts the credentials, the address and returns the new session", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));

    await expect(register("alice", "secret123", "alice@example.com")).resolves.toMatchObject({
      username: "alice",
      emailVerified: true,
    });

    expect(firstCall()[0]).toBe("/api/auth/register");
    expect(firstBody()).toEqual({
      username: "alice",
      password: "secret123",
      email: "alice@example.com",
    });
  });

  it("returns the pending account while the address waits to be confirmed", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));

    await expect(register("alice", "secret123", "alice@example.com")).resolves.toMatchObject({
      authenticated: false,
      username: "alice",
      quota: null,
      email: "alice@example.com",
      emailVerified: false,
    });
  });

  it("reports a taken username with the backend message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("username_taken", "该用户名已被占用。"), 409),
    );

    await expect(register("alice", "secret123", "alice@example.com")).rejects.toMatchObject({
      code: "username_taken",
      message: "该用户名已被占用。",
      status: 409,
    });
  });

  it("reports a taken address with the backend message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("email_taken", "该邮箱已被其他账号使用。"), 409),
    );

    await expect(register("alice", "secret123", "alice@example.com")).rejects.toMatchObject({
      code: "email_taken",
      message: "该邮箱已被其他账号使用。",
      status: 409,
    });
  });

  it("reports a rejected payload as invalid_request", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("invalid_request", "请求格式无效。"), 400));

    await expect(register("ab", "secret123", "abc@example.com")).rejects.toMatchObject({
      code: "invalid_request",
      status: 400,
    });
  });
});

describe("requestVerification", () => {
  it("asks for another link for the address the account supplied", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));

    await expect(
      requestVerification("alice", "secret123", "alice@example.com"),
    ).resolves.toMatchObject({ authenticated: false, emailVerified: false });

    expect(firstCall()[0]).toBe("/api/auth/verify/request");
    expect(firstBody()).toEqual({
      username: "alice",
      password: "secret123",
      email: "alice@example.com",
    });
  });

  it("leaves the address out so the stored one is reused", async () => {
    fetchMock.mockResolvedValue(jsonResponse(pendingBody("alice@example.com")));

    await requestVerification("alice", "secret123", "");

    expect(firstBody()).toEqual({ username: "alice", password: "secret123" });
  });

  it("surfaces a resend that came too soon", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("verification_throttled", "验证邮件刚刚发送过。请稍后再试。"), 429),
    );

    await expect(requestVerification("alice", "secret123", "")).rejects.toMatchObject({
      code: "verification_throttled",
      status: 429,
    });
  });
});

describe("login", () => {
  it("posts the credentials to the login endpoint", async () => {
    fetchMock.mockResolvedValue(jsonResponse(sessionBody()));

    await login("alice", "secret123");

    expect(firstCall()[0]).toBe("/api/auth/login");
    expect(firstBody()).toEqual({ username: "alice", password: "secret123" });
  });

  it("reports wrong credentials", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("invalid_credentials", "用户名或密码不正确。"), 401),
    );

    await expect(login("alice", "wrong")).rejects.toMatchObject({
      code: "invalid_credentials",
      status: 401,
    });
  });
});

describe("logout", () => {
  it("posts to the logout endpoint and resolves without a body", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ authenticated: false, username: null, quota: null }),
    );

    await expect(logout()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
  });

  it("surfaces a server refusal", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("internal_error", "服务暂时不可用。"), 500));

    await expect(logout()).rejects.toMatchObject({ code: "internal_error", status: 500 });
  });
});

describe("network failures", () => {
  const calls: readonly (readonly [string, () => Promise<unknown>])[] = [
    ["fetchSession", fetchSession],
    ["login", () => login("alice", "secret123")],
    ["register", () => register("alice", "secret123", "alice@example.com")],
    ["requestVerification", () => requestVerification("alice", "secret123", "")],
    ["logout", logout],
  ];

  for (const [name, call] of calls) {
    it(`${name} reports a network failure as AuthError`, async () => {
      fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

      await expect(call()).rejects.toMatchObject({
        code: "network_error",
        message: "网络请求失败，请检查连接后重试。",
        status: 0,
      });
    });
  }

  it("tolerates a response body that is not JSON at all", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: () => Promise.reject(new SyntaxError("Unexpected token <")),
    });

    await expect(fetchSession()).rejects.toMatchObject({ code: "request_failed", status: 502 });
  });
});

describe("fetchStats", () => {
  function reportBody(): unknown {
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

  it("reads the report with the session cookie and no request body", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportBody()));

    const report = await fetchStats();

    expect(report.windows.map((window) => window.key)).toEqual([
      "hour",
      "day",
      "week",
      "month",
      "total",
    ]);
    expect(report.windows[0]?.downloads).toBe(3);
    const [path, init] = firstCall();
    expect(path).toBe("/api/stats");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBeUndefined();
  });

  it("rejects a payload whose windows are not a list", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ windows: "soon" }));

    await expect(fetchStats()).rejects.toThrow("服务返回了无法识别的结果。");
  });

  it("rejects a window the server never publishes", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ windows: [{ key: "decade", downloads: 1, pages: 1, users: 1 }] }),
    );

    await expect(fetchStats()).rejects.toThrow(AuthError);
  });

  it("rejects a count that could not be a tally", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ windows: [{ key: "hour", downloads: -1, pages: 1, users: 1 }] }),
    );

    await expect(fetchStats()).rejects.toThrow(AuthError);
  });

  it("surfaces the server envelope when the endpoint fails", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("not_found", "没有这个资源。"), 404));

    await expect(fetchStats()).rejects.toMatchObject({
      code: "not_found",
      message: "没有这个资源。",
      status: 404,
    });
  });

  it("reports a network failure in words", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(fetchStats()).rejects.toThrow("网络请求失败，请检查连接后重试。");
  });
});
