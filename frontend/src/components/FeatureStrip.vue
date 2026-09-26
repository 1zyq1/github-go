<script setup lang="ts">
interface FeatureItem {
  readonly title: string;
  readonly detail: string;
}

const items: readonly FeatureItem[] = [
  {
    title: "安全代理",
    detail: "只允许 GitHub 官方域名，文件与页面之外一律拒绝。",
  },
  {
    title: "流式传输",
    detail: "保留 Range 与分块响应，大文件支持断点续传。",
  },
  {
    title: "不记录地址",
    detail: "提交的地址只存在于当前页面内存中。",
  },
];
</script>

<template>
  <section class="feature-strip" aria-label="服务特性">
    <div v-for="item in items" :key="item.title" class="feature-strip__item">
      <span class="feature-strip__icon" aria-hidden="true">
        <svg
          v-if="item.title === '安全代理'"
          viewBox="0 0 20 20"
          width="var(--space-5)"
          height="var(--space-5)"
          focusable="false"
        >
          <path
            d="M10 2.5 4 4.6v4.6c0 3.7 2.5 6.6 6 7.8 3.5-1.2 6-4.1 6-7.8V4.6z"
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linejoin="round"
          />
        </svg>
        <svg
          v-else-if="item.title === '流式传输'"
          viewBox="0 0 20 20"
          width="var(--space-5)"
          height="var(--space-5)"
          focusable="false"
        >
          <path
            d="M2.5 7h11m0 0-3-3m3 3-3 3M17.5 13h-11m0 0 3-3m-3 3 3 3"
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <svg
          v-else
          viewBox="0 0 20 20"
          width="var(--space-5)"
          height="var(--space-5)"
          focusable="false"
        >
          <path
            d="M3 10s2.6-4 7-4 7 4 7 4-2.6 4-7 4-7-4-7-4Z"
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linejoin="round"
          />
          <path
            d="M4.5 15.5 15.5 4.5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.75"
            stroke-linecap="round"
          />
        </svg>
      </span>
      <div class="feature-strip__text">
        <h2 class="feature-strip__title">{{ item.title }}</h2>
        <p class="feature-strip__detail">{{ item.detail }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.feature-strip {
  display: grid;
  gap: var(--space-4);
  max-width: var(--layout-max-width);
  margin-inline: auto;
  padding: var(--space-6) var(--gutter-mobile) 0;
}

.feature-strip__item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: var(--border-hairline);
}

.feature-strip__icon {
  flex-shrink: 0;
  color: var(--accent);
}

.feature-strip__text {
  display: grid;
  gap: var(--space-1);
}

.feature-strip__title {
  font-size: var(--text-small-size);
  font-weight: 650;
  line-height: var(--text-small-leading);
}

.feature-strip__detail {
  color: var(--text-secondary);
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
}

@media (min-width: 48rem) {
  .feature-strip {
    padding-inline: var(--gutter-tablet);
  }
}

@media (min-width: 64rem) {
  .feature-strip {
    grid-template-columns: 1.2fr 1.6fr 1.2fr;
    padding-inline: var(--gutter-desktop);
  }

  .feature-strip__item {
    border-top: none;
    padding-top: 0;
    padding-left: var(--space-5);
    border-left: var(--border-hairline);
  }

  .feature-strip__item:first-child {
    padding-left: 0;
    border-left: none;
  }
}
</style>
