// The theme bootstrap runs before the app mounts, so it is imported first.
// See src/theme-init.ts for why it is a module instead of an inline script.
import "./theme-init";
import { createApp } from "vue";
import App from "./App.vue";
import "./styles/fonts.css";
import "./styles/main.css";

createApp(App).mount("#app");
