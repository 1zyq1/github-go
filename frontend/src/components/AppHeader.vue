<script setup lang="ts">
import { computed } from "vue";
import { BLOG_URL, type Quota } from "../api/client";
import NetworkMark from "./NetworkMark.vue";

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
    <div class="app-header__brand">
      <NetworkMark />
      <div class="app-header__text">
        <h1 class="app-header__title">GitHub 文件加速</h1>
        <p class="app-header__purpose">
          把 Release、归档与 raw / blob 链接解析为同源下载，并把 GitHub 页面镜像为同源地址，保留
          Range 与流式传输。
        </p>
      </div>
    </div>

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
        <button class="button button--quiet app-header__logout" type="button" @click="requestLogout">
          退出登录
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: grid;
  gap: var(--space-4);
  max-width: var(--layout-max-width);
  margin-inline: auto;
  padding: var(--space-8) var(--gutter-mobile) var(--space-6);
}

.app-header__brand {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-width: 0;
}

.app-header__text {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}

.app-header__title {
  font-size: var(--text-subheading-size);
  font-weight: 650;
  line-height: var(--text-subheading-leading);
  letter-spacing: -0.01em;
}

.app-header__purpose {
  max-width: var(--text-measure);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.app-header__aside {
  display: grid;
  justify-items: start;
  gap: var(--space-3);
  min-width: 0;
}

.app-header__nav {
  display: flex;
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
  font-weight: 600;
  line-height: 1;
  text-decoration: none;
  transition:
    background-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.app-header__link:hover {
  background-color: var(--surface-sunken);
  color: var(--text-primary);
}

/* The current entry carries a tint as well as a colour, so the state is never
   signalled by colour alone. */
.app-header__link--current {
  background-color: var(--accent-soft);
  color: var(--accent);
}

/* The chip sits on the canvas: no raised surface, so nothing nests. */
.app-header__session {
  display: grid;
  justify-items: start;
  gap: var(--space-1);
  min-width: 0;
}

.app-header__user {
  font-family: var(--font-mono);
  font-size: var(--text-small-size);
  font-weight: 650;
  line-height: var(--text-small-leading);
  overflow-wrap: anywhere;
}

.app-header__quota {
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
}

.app-header__quota--exhausted {
  color: var(--status-warning);
}

.app-header__reset {
  color: var(--text-muted);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
}

.app-header__logout {
  margin-top: var(--space-1);
}

@media (min-width: 48rem) {
  .app-header {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: start;
    column-gap: var(--space-8);
    padding: var(--space-16) var(--gutter-tablet) var(--space-8);
  }

  .app-header__title {
    font-size: var(--text-heading-size);
    line-height: var(--text-heading-leading);
  }

  .app-header__purpose {
    font-size: var(--text-body-size);
    line-height: var(--text-body-leading);
  }

  .app-header__aside {
    justify-items: end;
  }

  .app-header__session {
    justify-items: end;
    text-align: right;
  }
}

@media (min-width: 64rem) {
  .app-header {
    padding-inline: var(--gutter-desktop);
  }
}
</style>
