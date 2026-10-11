import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import StatsPanel from "../src/components/StatsPanel.vue";

const fetchMock = vi.fn<(input: string, init: RequestInit) => Promise<unknown>>();

function jsonResponse(body: unknown, status = 200): unknown {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}

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

async function renderPanel(): Promise<VueWrapper> {
  const view = mount(StatsPanel);
  await flushPromises();
  return view;
}

const ROW_LABELS = ["1 小时", "24 小时", "7 天", "1 个月", "全部"];

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StatsPanel", () => {
  it("reports progress before the numbers arrive", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    const view = mount(StatsPanel);

    expect(view.get("[role='status']").text()).toBe("正在加载统计数据。");
    // The bars stand in for the table, but no cell carries a figure yet.
    expect(view.find("table[aria-hidden='true']").exists()).toBe(true);
    expect(view.find("[data-test='stats-downloads']").exists()).toBe(false);
    view.unmount();
  });

  it("reads the public report exactly once, with no session", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportBody()));
    const view = await renderPanel();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/stats");
    expect(view.find("[data-test='stats-hour']").exists()).toBe(true);
  });

  it("shows every window in the order the server sent them", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportBody()));
    const view = await renderPanel();

    const rows = view.findAll("tbody tr");
    expect(rows).toHaveLength(5);
    expect(rows.map((row) => row.get("th[scope='row']").text())).toEqual(ROW_LABELS);
  });

  it("shows the counts as returned, with nothing derived", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportBody()));
    const view = await renderPanel();

    const hour = view.get("[data-test='stats-hour']");
    expect(hour.get("[data-test='stats-downloads']").text()).toBe("3");
    expect(hour.get("[data-test='stats-pages']").text()).toBe("1");
    expect(hour.get("[data-test='stats-users']").text()).toBe("2");
    expect(view.get("[data-test='stats-total'] [data-test='stats-downloads']").text()).toBe("90");
  });

  it("keeps the table out of the way when the report cannot be read", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const view = await renderPanel();

    expect(view.get("[role='alert']").text()).toContain("网络请求失败");
    expect(view.find("table").exists()).toBe(false);
  });

  it("labels the table for assistive technology", async () => {
    fetchMock.mockResolvedValue(jsonResponse(reportBody()));
    const view = await renderPanel();

    expect(view.get("table caption").text()).toContain("按时间范围统计");
    expect(view.get("section").attributes("aria-labelledby")).toBe("stats-title");
  });
});
