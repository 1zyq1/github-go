<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  AuthError,
  BLOG_URL,
  BLOG_WALLET_URL,
  logout as endSession,
  fetchSession,
  fetchSsoProviders,
  type Session,
  type SsoProviders,
} from "./api/client";
import AppHeader from "./components/AppHeader.vue";
import AuthPanel from "./components/AuthPanel.vue";
import FeatureStrip from "./components/FeatureStrip.vue";
import StatsPanel from "./components/StatsPanel.vue";
import UrlConverter from "./components/UrlConverter.vue";

type SessionState = "loading" | "anonymous" | "authenticated";
type Page = "accelerator" | "stats";

const SESSION_UNAVAILABLE_MESSAGE = "无法确认登录状态，请刷新页面重试。";
const SESSION_EXPIRED_MESSAGE = "登录状态已失效，请重新登录。";

/**
 * Both pages ship in one bundle, so the path is read once instead of pulling in
 * a router for a single branch. The trailing slash is dropped because the
 * server serves the shell for both forms.
 */
function readPage(): Page {
  const path = window.location.pathname.replace(/\/+$/, "");
  return path === "/stats" ? "stats" : "accelerator";
}

const page = ref<Page>(readPage());

const sessionState = ref<SessionState>("loading");
const session = ref<Session | null>(null);
const limited = ref(false);
const sessionError = ref("");
/** The verification mail was followed successfully. */
const confirmed = ref(false);
/** The verification link was already spent or has expired. */
const verifyFailed = ref(false);
/** The blog SSO sign-in completed successfully. */
const ssoConfirmed = ref(false);
/** The blog SSO sign-in failed with this message. */
const ssoError = ref("");
/** A pending return to the SSO authorize URL after a local sign-in. */
const ssoReturn = ref("");
/** Which cross-site buttons the backend serves (SSO sign-in, redeem). */
const providers = ref<SsoProviders>({ blog: false, redeemEnabled: false });
/** Redeemed on the blog wallet and returned here with success. */
const redeemConfirmed = ref(false);

const quotaExhausted = computed(() => {
  const quota = session.value?.quota ?? null;
  return quota !== null && quota.remaining <= 0;
});

/** Re-read the session from the server; the server is the only authority on it. */
async function refreshSession(): Promise<void> {
  try {
    const next = await fetchSession();
    session.value = next;
    sessionState.value = next.authenticated ? "authenticated" : "anonymous";
    sessionError.value = "";
    const quota = next.quota;
    if (quota === null || quota.remaining > 0) {
      limited.value = false;
    }
    if (next.authenticated && ssoReturn.value) {
      // A blog sign-in was waiting for a local session: finish the SSO handshake.
      const target = ssoReturn.value;
      ssoReturn.value = "";
      if (target.startsWith("/") && !target.startsWith("//")) {
        window.location.replace(target);
        return;
      }
    }
  } catch {
    session.value = null;
    sessionState.value = "anonymous";
    sessionError.value = SESSION_UNAVAILABLE_MESSAGE;
  }
}

onMounted((): void => {
  // The server redirects here with a flag so the outcome survives the hop out of the mail client.
  const parameters = new URLSearchParams(window.location.search);
  limited.value = parameters.get("limited") === "1";
  confirmed.value = parameters.get("verified") === "1";
  verifyFailed.value = parameters.get("verify_failed") === "1";
  // The SSO outcome (sso=ok|error) and the pending authorize return (sso_return).
  ssoConfirmed.value = parameters.get("sso") === "ok";
  if (parameters.get("sso") === "error") {
    ssoError.value = parameters.get("message") || "博客账号登录失败，请重试。";
  }
  const returnTo = parameters.get("sso_return") ?? "";
  if (returnTo.startsWith("/") && !returnTo.startsWith("//")) {
    ssoReturn.value = returnTo;
  }
  // Returned from the blog wallet after a successful redeem.
  redeemConfirmed.value = parameters.get("redeem") === "ok";
  void fetchSsoProviders().then((next) => {
    providers.value = next;
  });
  void refreshSession();
});

async function handleLogout(): Promise<void> {
  try {
    await endSession();
  } catch (error: unknown) {
    sessionError.value = error instanceof AuthError ? error.message : SESSION_UNAVAILABLE_MESSAGE;
    return;
  }
  session.value = null;
  sessionState.value = "anonymous";
  limited.value = false;
  sessionError.value = "";
}

async function handleUnauthorized(): Promise<void> {
  await refreshSession();
  sessionError.value = SESSION_EXPIRED_MESSAGE;
}
</script>

<template>
  <div class="page">
    <AppHeader
      :username="session?.username ?? null"
      :quota="session?.quota ?? null"
      :page="page"
      @logout="handleLogout"
    />
    <div class="page__body">
      <main id="main" class="page__main">
        <!-- The header carries the brand, so the page opens with a lead line —
             the way the blog's inner pages do. -->
        <p v-if="page === 'accelerator'" class="page__lead">
          把 Release、归档与 raw / blob 链接解析为同源下载，并把 GitHub 页面镜像为同源地址，保留
          Range 与流式传输。
        </p>
        <p v-if="sessionError" class="notice notice--warning" role="alert">{{ sessionError }}</p>
        <StatsPanel v-if="page === 'stats'" />
        <template v-else>
          <p v-if="confirmed" class="notice" role="status" data-test="verified-notice">
            邮箱验证成功，已自动登录。
          </p>
          <p
            v-if="verifyFailed"
            class="notice notice--warning"
            role="alert"
            data-test="verify-failed"
          >
            验证链接无效或已过期，请重新获取。
          </p>
          <p v-if="ssoConfirmed" class="notice" role="status" data-test="sso-notice">
            已通过博客账号登录。
          </p>
          <p v-if="ssoError" class="notice notice--warning" role="alert" data-test="sso-error">
            {{ ssoError }}
          </p>
          <p
            v-if="limited || quotaExhausted"
            class="notice notice--warning"
            role="alert"
            data-test="quota-notice"
          >
            本小时的访问额度已用完，请在额度重置后再试。
          </p>
          <p v-if="redeemConfirmed" class="redeem__notice" role="status" data-test="redeem-notice">
            已在博客钱包完成兑换，额度已恢复。
          </p>
          <div
            v-if="quotaExhausted && sessionState === 'authenticated' && providers.redeemEnabled"
            class="redeem"
            data-test="redeem-panel"
          >
            <p class="redeem__lead">
              额度用完了，去博客钱包用博客积分兑换额外次数（那儿能看到余额和积分是怎么来的）。
            </p>
            <a
              class="button button--primary redeem__button"
              :href="BLOG_WALLET_URL"
              data-test="redeem-button"
            >
              去博客钱包兑换
            </a>
          </div>
          <template v-if="sessionState === 'loading'">
            <!-- The wait is announced in words; the card below wears the same
                 frame and rhythm as the panel that replaces it, so the landing
                 state drops in without shifting the page. -->
            <p class="page__status sr-only" role="status">正在检查登录状态。</p>
            <div class="session-skeleton" aria-hidden="true">
              <span class="skeleton session-skeleton__title"></span>
              <span class="skeleton session-skeleton__lead"></span>
              <span class="skeleton session-skeleton__lead session-skeleton__lead--short"></span>
              <span class="skeleton session-skeleton__segment"></span>
              <span class="skeleton session-skeleton__field"></span>
              <span class="skeleton session-skeleton__field"></span>
              <span class="skeleton session-skeleton__button"></span>
            </div>
          </template>
          <AuthPanel v-else-if="sessionState === 'anonymous'" @authenticated="refreshSession" />
          <UrlConverter
            v-else
            :disabled="quotaExhausted"
            @resolved="refreshSession"
            @unauthorized="handleUnauthorized"
            @quota-exceeded="refreshSession"
          />
        </template>
      </main>
      <FeatureStrip v-if="page === 'accelerator'" />
    </div>
    <footer class="page__footer">
      <p>仅代理 GitHub 官方文件与页面，不记录用户提交的地址。</p>
      <a class="page__footer-link" :href="BLOG_URL" target="_blank" rel="noopener noreferrer">
        访问博客
      </a>
    </footer>
  </div>
</template>

<style scoped>
/* Stand-in for the panel that replaces it once the session is known: the same
   frame, padding and control heights, so switching between the skeleton, the
   sign-in panel and the converter never moves the page. */
.session-skeleton {
  display: grid;
  gap: var(--space-4);
  padding: var(--space-6);
  border: var(--border-hairline);
  border-radius: var(--radius-surface);
  background-color: var(--surface-raised);
}

.session-skeleton__title {
  width: 50%;
  height: var(--space-5);
}

.session-skeleton__lead {
  height: var(--space-3);
}

.session-skeleton__lead--short {
  width: 65%;
}

.session-skeleton__segment,
.session-skeleton__field {
  height: var(--target-min);
}

.session-skeleton__button {
  width: var(--space-20);
  height: var(--target-min);
}

.redeem {
  display: grid;
  gap: var(--space-3);
  justify-items: start;
}

.redeem__lead {
  color: var(--text-secondary);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.redeem__notice {
  color: var(--status-success);
  font-size: var(--text-small-size);
  line-height: var(--text-small-leading);
}

.redeem__button {
  justify-self: start;
}
</style>
