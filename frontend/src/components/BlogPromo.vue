<script setup lang="ts">
import { BLOG_URL } from "../api/client";
</script>

<template>
  <aside class="blog-promo" aria-labelledby="blog-promo-title">
    <div class="blog-promo__card">
      <div class="blog-promo__text">
        <h2 class="blog-promo__title" id="blog-promo-title">1zyq1 的博客</h2>
        <p class="blog-promo__detail">
          分享 AI 绘图、证件照、语音合成等技术玩法和心得，欢迎来逛逛。
        </p>
      </div>
      <a
        class="button button--primary blog-promo__link"
        :href="BLOG_URL"
        target="_blank"
        rel="noopener noreferrer"
      >
        访问博客
      </a>
    </div>
  </aside>
</template>

<style scoped>
.blog-promo {
  max-width: var(--layout-max-width);
  margin-inline: auto;
  padding: var(--space-6) var(--gutter-mobile) 0;
}

.blog-promo__card {
  display: grid;
  gap: var(--space-4);
  padding: var(--space-5);
  border: var(--border-hairline);
  border-radius: var(--radius-surface);
  background-color: var(--surface-raised);
}

.blog-promo__text {
  display: grid;
  gap: var(--space-1);
}

.blog-promo__title {
  font-size: var(--text-subheading-size);
  font-weight: 650;
  line-height: var(--text-subheading-leading);
}

.blog-promo__detail {
  max-width: var(--text-measure);
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.blog-promo__link {
  justify-self: start;
}

@media (min-width: 48rem) {
  .blog-promo {
    padding-inline: var(--gutter-tablet);
  }

  .blog-promo__card {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    padding-inline: var(--space-6);
  }

  .blog-promo__link {
    justify-self: end;
  }
}

@media (min-width: 64rem) {
  .blog-promo {
    padding-inline: var(--gutter-desktop);
  }
}
</style>
