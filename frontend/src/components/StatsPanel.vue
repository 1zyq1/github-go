<script setup lang="ts">
import { onMounted, ref } from "vue";
import { AuthError, fetchStats, type StatsReport, type StatWindowKey } from "../api/client";

const UNEXPECTED_ERROR_MESSAGE = "统计加载失败，请稍后重试。";

/**
 * The rows are the server's windows in the server's order, so the labels are a
 * lookup rather than a list this component owns. A missing key is impossible
 * because the response is validated before it gets here.
 */
const WINDOW_LABELS: Record<StatWindowKey, string> = {
  hour: "1 小时",
  day: "24 小时",
  week: "7 天",
  month: "1 个月",
  total: "全部",
};

const report = ref<StatsReport | null>(null);
const errorMessage = ref("");
const loading = ref(true);

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    report.value = await fetchStats();
  } catch (error: unknown) {
    report.value = null;
    errorMessage.value = error instanceof AuthError ? error.message : UNEXPECTED_ERROR_MESSAGE;
  } finally {
    loading.value = false;
  }
}

onMounted((): void => {
  void load();
});
</script>

<template>
  <section class="stats" aria-labelledby="stats-title">
    <h2 class="stats__title" id="stats-title">使用统计</h2>
    <p class="stats__lead">
      每个时间范围各自统计。下载是文件代理的实际交付，浏览是镜像页面的实际交付，用户是该范围内去重的登录账号。
    </p>
    <p v-if="loading" class="page__status" role="status">正在加载统计数据。</p>
    <p v-else-if="errorMessage" class="notice notice--warning" role="alert">{{ errorMessage }}</p>
    <table v-else-if="report" class="stats__table">
      <caption class="stats__caption">
        按时间范围统计的下载次数、页面浏览与访问用户
      </caption>
      <thead>
        <tr>
          <th scope="col">范围</th>
          <th scope="col">下载</th>
          <th scope="col">浏览</th>
          <th scope="col">用户</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="window in report.windows"
          :key="window.key"
          :class="{ 'stats__row--total': window.key === 'total' }"
          :data-test="`stats-${window.key}`"
        >
          <th scope="row">{{ WINDOW_LABELS[window.key] }}</th>
          <td data-test="stats-downloads">{{ window.downloads }}</td>
          <td data-test="stats-pages">{{ window.pages }}</td>
          <td data-test="stats-users">{{ window.users }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.stats {
  display: grid;
  gap: var(--space-5);
  padding: var(--space-6);
  border: var(--border-hairline);
  border-radius: var(--radius-surface);
  background-color: var(--surface-raised);
}

.stats__title {
  font-size: var(--text-subheading-size);
  font-weight: 650;
  line-height: var(--text-subheading-leading);
}

.stats__lead {
  max-width: var(--text-measure);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.stats__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

/* The heading directly above already names the table, so the caption is kept
   for assistive technology only. */
.stats__caption {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  overflow: hidden;
  white-space: nowrap;
  clip-path: inset(50%);
}

.stats__table th,
.stats__table td {
  padding: var(--space-2);
  text-align: left;
  border-bottom: var(--border-hairline);
}

.stats__table thead th {
  color: var(--text-muted);
  font-weight: 600;
}

.stats__table tbody th {
  font-weight: 600;
}

.stats__table td {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.stats__row--total th,
.stats__row--total td {
  font-weight: 700;
}

.stats__table tbody tr:last-child th,
.stats__table tbody tr:last-child td {
  border-bottom: 0;
}

@media (min-width: 48rem) {
  .stats__table th,
  .stats__table td {
    padding: var(--space-3);
  }
}
</style>
