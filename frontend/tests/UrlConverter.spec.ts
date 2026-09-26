import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UrlConverter from "../src/components/UrlConverter.vue";

const RELEASE_URL = "https://github.com/acme/widgets/releases/download/v1.0.0/app.zip";
const ARCHIVE_URL = "https://github.com/acme/widgets/archive/refs/heads/main.zip";
const RAW_URL = "https://raw.githubusercontent.com/acme/widgets/main/file.txt";
const BLOB_URL = "https://github.com/acme/widgets/blob/main/file.txt";
const PAGE_URL = "https://github.com/acme/widgets";
const PROFILE_URL = "https://github.com/1zyq1";

type Kind = "release" | "archive" | "raw";
type PageKind = "page";
type AnyKind = Kind | PageKind;

const fetchMock = vi.fn<(path: string, init: RequestInit) => Promise<unknown>>();

function jsonResponse(body: unknown, status = 200): unknown {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

function resolvedBody(kind: AnyKind, sourceUrl: string, proxyUrl = "/proxy/token-abc"): unknown {
  return { kind, source_url: sourceUrl, proxy_url: proxyUrl };
}

function errorBody(code: string, message: string): unknown {
  return { error: { code, message } };
}

function setClipboard(writeText: ((text: string) => Promise<void>) | undefined): void {
  Object.defineProperty(window.navigator, "clipboard", {
    value: writeText === undefined ? undefined : { writeText },
    configurable: true,
    writable: true,
  });
}

function currentUrlCall(): { path: string; init: RequestInit } {
  const call = fetchMock.mock.calls.at(0);
  if (call === undefined) {
    throw new Error("resolveSource did not call fetch");
  }
  return { path: call[0], init: call[1] };
}

async function submit(view: VueWrapper, value: string): Promise<void> {
  await view.get("input#source-url").setValue(value);
  await view.get("form").trigger("submit");
  await flushPromises();
}

function mountConverter(attachToDocument = false): VueWrapper {
  return attachToDocument ? mount(UrlConverter, { attachTo: document.body }) : mount(UrlConverter);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window.navigator, "clipboard");
});

describe("UrlConverter", () => {
  it("starts idle with a labelled form and an empty result panel", () => {
    const view = mountConverter();

    expect(view.get("label[for='source-url']").text()).toBe("GitHub 主页名或项目地址");
    expect(view.get<HTMLInputElement>("input#source-url").element.value).toBe("");
    expect(view.get("[data-test='result-panel']").attributes("data-state")).toBe("empty");
    expect(view.get("form").get("button[type='submit']").attributes("disabled")).toBeUndefined();
  });

  it("trims surrounding whitespace before calling the same-origin API", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();

    await submit(view, `   ${RELEASE_URL}  `);

    const { path, init } = currentUrlCall();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(path).toBe("/api/resolve");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe(JSON.stringify({ url: RELEASE_URL }));
  });

  it.each([
    ["1zyq1", "https://github.com/1zyq1"],
    ["acme/widgets", "https://github.com/acme/widgets"],
    ["github.com/acme/widgets", "https://github.com/acme/widgets"],
    [
      "raw.githubusercontent.com/acme/widgets/main/README.md",
      "https://raw.githubusercontent.com/acme/widgets/main/README.md",
    ],
    ["https://github.com/acme/widgets?tab=readme", "https://github.com/acme/widgets?tab=readme"],
  ] as const)("completes the shorthand %s into %s", async (typed, expected) => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("page", expected, "/acme/widgets")));
    const view = mountConverter();

    await submit(view, typed);

    expect(currentUrlCall().init.body).toBe(JSON.stringify({ url: expected }));
  });

  it("renders a release asset result with a safe new-tab download action", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("success");
    expect(panel.text()).toContain("Release 资产");
    expect(panel.text()).toContain("app.zip");
    const link = panel.get("a[data-test='download-link']");
    expect(link.attributes("href")).toBe("/proxy/token-abc");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toBe("noopener noreferrer");
  });

  it.each([
    ["archive", ARCHIVE_URL, "源码归档", "widgets-main.zip"],
    ["raw", RAW_URL, "文件内容", "file.txt"],
    ["raw", BLOB_URL, "文件内容", "file.txt"],
  ] as const)("labels %s sources from %s", async (kind, sourceUrl, label, fileName) => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody(kind, sourceUrl)));
    const view = mountConverter();

    await submit(view, sourceUrl);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.text()).toContain(label);
    expect(panel.text()).toContain(fileName);
  });

  it.each([
    [PAGE_URL, "/acme/widgets"],
    [PROFILE_URL, "/1zyq1"],
  ] as const)("renders %s as a mirror page action", async (sourceUrl, mirrorPath) => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("page", sourceUrl, mirrorPath)));
    const view = mountConverter();

    await submit(view, sourceUrl);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("success");
    expect(panel.text()).toContain("镜像页面");
    expect(panel.text()).toContain("可以在镜像中打开");
    expect(panel.text()).not.toContain("在新标签页下载");
    expect(panel.get("[data-test='mirror-address']").text()).toBe(
      `http://localhost:3000${mirrorPath}`,
    );
    const link = panel.get("a[data-test='mirror-link']");
    expect(link.text()).toBe("打开反代地址");
    expect(link.attributes("href")).toBe(mirrorPath);
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toBe("noopener noreferrer");
    expect(panel.find("[data-test='download-link']").exists()).toBe(false);
  });

  it("does not show a mirror address line for a file result", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.find("[data-test='mirror-address']").exists()).toBe(false);
  });

  it("keeps the page query string on the mirror link", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        resolvedBody(
          "page",
          "https://github.com/acme/widgets?tab=readme",
          "/acme/widgets?tab=readme",
        ),
      ),
    );
    const view = mountConverter();

    await submit(view, "https://github.com/acme/widgets?tab=readme");

    const link = view.get("a[data-test='mirror-link']");
    expect(link.attributes("href")).toBe("/acme/widgets?tab=readme");
  });

  it("copies the absolute mirror address for a page result", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve());
    setClipboard(writeText);
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("page", PAGE_URL, "/acme/widgets")));
    const view = mountConverter();

    await submit(view, PAGE_URL);
    await view.get("[data-test='copy-button']").trigger("click");
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("http://localhost:3000/acme/widgets");
    expect(view.get("[data-test='copy-feedback']").text()).toBe("已复制到剪贴板。");
  });

  it.each([
    ["/api/widgets"],
    ["/proxy/token-abc"],
    ["/assets/index.js"],
    ["/fonts/app.woff2"],
    ["//evil.example/widgets"],
    ["https://evil.example/widgets"],
  ] as const)("refuses a page mirror path that is not safely same-origin: %s", async (badPath) => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("page", PAGE_URL, badPath)));
    const view = mountConverter();

    await submit(view, PAGE_URL);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("服务返回了无法识别的结果。");
    expect(panel.find("a[data-test='mirror-link']").exists()).toBe(false);
  });

  it("refuses a file result whose path is not a proxy path", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(resolvedBody("release", RELEASE_URL, "/acme/widgets")),
    );
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("服务返回了无法识别的结果。");
  });

  it("renders the backend error envelope without leaking internals", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("invalid_url", "该地址格式无效。"), 400));
    const view = mountConverter();

    await submit(view, "https://github.com/acme");

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("该地址格式无效。");
    expect(panel.text()).not.toContain("Traceback");
    expect(panel.find("[data-test='download-link']").exists()).toBe(false);
  });

  it("reports a network failure without a stack trace", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    const panel = view.get("[data-test='result-panel']");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("网络请求失败");
    expect(panel.text()).not.toContain("Failed to fetch");
  });

  it("prevents a double submit while a resolve is in flight", async () => {
    let release: (value: unknown) => void = () => undefined;
    fetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const view = mountConverter();

    await view.get("input#source-url").setValue(RELEASE_URL);
    await view.get("form").trigger("submit");
    await view.get("form").trigger("submit");
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(view.get("form").get("button[type='submit']").attributes("disabled")).toBeDefined();

    release(jsonResponse(resolvedBody("release", RELEASE_URL)));
    await flushPromises();
    expect(view.get("[data-test='result-panel']").attributes("data-state")).toBe("success");
  });

  it("resets to the idle state and keeps focus in the input", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter(true);
    await submit(view, RELEASE_URL);

    await view.get("[data-test='reset-button']").trigger("click");
    await flushPromises();

    expect(view.get("[data-test='result-panel']").attributes("data-state")).toBe("empty");
    expect(view.get<HTMLInputElement>("input#source-url").element.value).toBe("");
    expect(document.activeElement).toBe(view.get("input#source-url").element);
    view.unmount();
  });

  it("rejects an empty submission inline without calling the API", async () => {
    const view = mountConverter();

    await submit(view, "   ");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(view.get("[data-test='field-error']").text()).toContain("请填写");
    expect(view.get("input#source-url").attributes("aria-invalid")).toBe("true");
  });

  it("copies the same-origin download URL and confirms it", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve());
    setClipboard(writeText);
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();
    await submit(view, RELEASE_URL);

    await view.get("[data-test='copy-button']").trigger("click");
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("http://localhost:3000/proxy/token-abc");
    expect(view.get("[data-test='copy-feedback']").text()).toContain("已复制");
  });

  it("explains that copy failed when the clipboard API is unavailable", async () => {
    setClipboard(undefined);
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();
    await submit(view, RELEASE_URL);

    await view.get("[data-test='copy-button']").trigger("click");
    await flushPromises();

    expect(view.get("[data-test='copy-feedback']").text()).toContain("无法复制");
  });

  it("announces the resolved file through a polite live region", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();

    expect(view.get("[data-test='result-status']").attributes("aria-live")).toBe("polite");
    await submit(view, RELEASE_URL);

    const status = view.get("[data-test='result-status']");
    expect(status.attributes("role")).toBe("status");
    expect(status.text()).toContain("app.zip");
  });

  it("announces errors assertively without echoing the submitted URL", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(errorBody("unsupported_url", "暂不支持该地址类型。"), 400),
    );
    const view = mountConverter();

    expect(view.get("[data-test='field-error']").attributes("role")).toBe("alert");
    await submit(view, RELEASE_URL);

    const alert = view.get("[data-test='field-error']");
    expect(alert.text()).toContain("暂不支持该地址类型。");
    expect(alert.text()).not.toContain(RELEASE_URL);
  });
});

describe("UrlConverter access signalling", () => {
  it("reports a resolved link to the app", async () => {
    fetchMock.mockResolvedValue(jsonResponse(resolvedBody("release", RELEASE_URL)));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    expect(view.emitted("resolved")).toHaveLength(1);
    expect(view.emitted("unauthorized")).toBeUndefined();
    expect(view.emitted("quota-exceeded")).toBeUndefined();
  });

  it("asks the app to re-check the session on a 401", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("unauthorized", "请先登录后再使用。"), 401));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    expect(view.emitted("unauthorized")).toHaveLength(1);
    expect(view.get("[data-test='field-error']").text()).toContain("请先登录后再使用。");
  });

  it("asks the app to re-check the allowance on a 429", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        errorBody("quota_exceeded", "本小时已用完 10 次访问额度。请在 12 分钟后重试。"),
        429,
      ),
    );
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    expect(view.emitted("quota-exceeded")).toHaveLength(1);
  });

  it("does not report access problems for other failures", async () => {
    fetchMock.mockResolvedValue(jsonResponse(errorBody("not_found", "找不到该资源。"), 404));
    const view = mountConverter();

    await submit(view, RELEASE_URL);

    expect(view.emitted("unauthorized")).toBeUndefined();
    expect(view.emitted("quota-exceeded")).toBeUndefined();
  });

  it("refuses to submit while disabled and never calls the API", async () => {
    const view = mount(UrlConverter, { props: { disabled: true } });

    expect(view.get("form").get("button[type='submit']").attributes("disabled")).toBeDefined();

    await submit(view, RELEASE_URL);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(view.emitted("resolved")).toBeUndefined();
  });
});
