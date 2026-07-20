import { chromium } from "playwright";

const baseUrl = process.env.BASE_URL || "http://127.0.0.1:8765";
const browser = await chromium.launch();
const desktop = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  extraHTTPHeaders: { "X-Forwarded-For": "203.0.113.20" },
});
const page = await desktop.newPage();
await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);

const initial = {
  title: await page.title(),
  header: await page.locator(".chat-title h1").innerText(),
  subtitle: await page.locator(".subtitle").innerText(),
  headerLinkHidden: await page.isHidden(".header-link"),
  stepperHidden: await page.isHidden("#stepper"),
  chipsHidden: await page.isHidden("#chips"),
  panelHidden: await page.isHidden("#intake-panel"),
};
await page.screenshot({ path: "/tmp/wiki-chatbot-desktop.png", fullPage: true });

const before = await page.locator(".message-row-assistant").count();
await page.fill("#message-input", "문서 근거와 해석은 어떻게 구분하나요?");
await page.click("#send-button");
await page.waitForFunction(
  (count) => document.querySelectorAll(".message-row-assistant").length > count,
  before,
  { timeout: 15000 },
);
await page.waitForSelector(".typing", { state: "detached", timeout: 15000 });
await page.waitForFunction(() => document.querySelector("#turn-counter")?.textContent === "1/10");
const response = {
  reply: await page.locator(".message-row-assistant .message").last().innerText(),
  turn: await page.locator("#turn-counter").innerText(),
};

const mobile = await browser.newContext({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  extraHTTPHeaders: { "X-Forwarded-For": "203.0.113.21" },
});
const mobilePage = await mobile.newPage();
await mobilePage.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
await mobilePage.waitForTimeout(500);
await mobilePage.screenshot({ path: "/tmp/wiki-chatbot-mobile.png", fullPage: true });
const mobileWidth = await mobilePage.locator("body").evaluate((element) => element.scrollWidth);

if (initial.title !== "위키 지식 챗봇") throw new Error("wiki title mismatch");
if (!initial.headerLinkHidden || !initial.stepperHidden || !initial.chipsHidden || !initial.panelHidden) {
  throw new Error("intake UI is visible in wiki mode");
}
if (!response.reply.startsWith("[fake] 위키 근거:")) throw new Error("wiki grounding reply mismatch");
if (response.turn !== "1/10") throw new Error("turn counter mismatch");
if (mobileWidth !== 390) throw new Error(`mobile overflow: ${mobileWidth}`);

console.log(JSON.stringify({ initial, response, mobileWidth }, null, 2));
await browser.close();
