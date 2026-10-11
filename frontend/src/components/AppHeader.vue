<script setup lang="ts">
import { computed } from "vue";
import { BLOG_URL, type Quota } from "../api/client";
import GithubMark from "./GithubMark.vue";
import ThemeToggle from "./ThemeToggle.vue";

const props = defineProps<{
  username: string | null;
  quota: Quota | null;
  page: "accelerator" | "stats";
}>();
const emit = defineEmits<{ logout: [] }>();

const remaining = computed(() => props.quota?.remaining ?? null);

const quotaLabel = computed(() => {
  const left = remaining.value;
  const total = props.quota?.limit ?? null;
  return left === null || total === null ? "" : `剩余 ${left} / ${total} 次`;
});

const exhausted = computed(() => remaining.value !== null && remaining.value <= 0);

/**
 * The API only reports a reset time once a unit is actually blocking, so the
 * hint is shown when the allowance is spent and never while units remain.
 */
const resetLabel = computed(() => {
  const quota = props.quota;
  if (quota === null || quota.remaining > 0) {
    return "";
  }
  const minutes = Math.max(1, Math.ceil(quota.resetAfterSeconds / 60));
  return `额度将在 ${minutes} 分钟后重置`;
});

function requestLogout(): void {
  emit("logout");
}
</script>

<template>
  <header class="app-header">
    <!-- The brand doubles as the home link, the way the blog's header does. -->
    <a class="app-header__brand" href="/">
      <GithubMark />
      <h1 class="app-header__title">GitHub 文件加速</h1>
    </a>

    <div class="app-header__aside">
      <nav class="app-header__nav" aria-label="站点导航">
        <a
          class="app-header__link"
          :class="{ 'app-header__link--current': page === 'accelerator' }"
          :aria-current="page === 'accelerator' ? 'page' : undefined"
          href="/"
        >
          加速
        </a>
        <a
          class="app-header__link"
          :class="{ 'app-header__link--current': page === 'stats' }"
          :aria-current="page === 'stats' ? 'page' : undefined"
          href="/stats"
        >
          数据
        </a>
        <a
          class="app-header__link"
          :href="BLOG_URL"
          target="_blank"
          rel="noopener noreferrer"
        >
          博客
        </a>
      </nav>

      <ThemeToggle />

      <div v-if="username !== null" class="app-header__session" data-test="session-chip">
        <p class="app-header__user" data-test="session-user">{{ username }}</p>
        <p
          v-if="quotaLabel"
          class="app-header__quota"
          :class="{ 'app-header__quota--exhausted': exhausted }"
          data-test="session-quota"
        >
          {{ quotaLabel }}
        </p>
        <p v-if="resetLabel" class="app-header__reset" data-test="session-reset">
          {{ resetLabel }}
        </p>
        <button class="app-header__logout" type="button" @click="requestLogout">退出登录</button>
      </div>
    </div>
  </header>
</template>

<style scoped>
/* Sticky, translucent bar with a hairline rule — the blog's inner-page header.
   The brand sits left, navigation and account controls right; everything wraps
   instead of scrolling sideways, so 320px still fits. */
.app-header {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-2) var(--gutter-mobile);
  border-bottom: var(--border-hairline);
  background-color: var(--surface-canvas-blur);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.app-header__brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-inline-end: auto;
  color: inherit;
  text-decoration: none;
}

.app-header__title {
  font-size: var(--text-body-size);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.app-header__aside {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1) var(--space-3);
  min-width: 0;
}

.app-header__nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.app-header__link {
  display: inline-flex;
  align-items: center;
  min-height: var(--target-min);
  padding-inline: var(--space-3);
  border-radius: var(--radius-control);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  font-weight: 500;
  line-height: 1;
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.app-header__link:hover {
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

/* The current entry carries a tint as well as a colour, so the state is never
   signalled by colour alone. */
.app-header__link--current {
  background-color: var(--accent-soft);
  color: var(--text-primary);
  font-weight: 600;
}

.app-header__session {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1) var(--space-3);
  min-width: 0;
}

.app-header__user {
  font-family: var(--font-mono);
  font-size: var(--text-small-size);
  font-weight: 600;
  line-height: var(--text-small-leading);
  overflow-wrap: anywhere;
}

.app-header__quota,
.app-header__reset {
  color: var(--text-muted);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
  white-space: nowrap;
}

.app-header__quota--exhausted {
  color: var(--status-warning);
}

.app-header__logout {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: var(--target-min);
  padding-inline: var(--space-3);
  border: var(--border-hairline);
  border-radius: var(--radius-control);
  background-color: transparent;
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  transition:
    background-color var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.app-header__logout:hover {
  border-color: var(--border-strong);
  background-color: var(--surface-hover);
  color: var(--text-primary);
}

@media (min-width: 48rem) {
  .app-header {
    padding-inline: var(--gutter-tablet);
  }
}

@media (min-width: 64rem) {
  .app-header {
    padding-inline: var(--gutter-desktop);
  }
}
</style>
