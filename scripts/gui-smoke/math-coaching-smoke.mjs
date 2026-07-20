import { chromium } from "playwright";

const browser = await chromium.launch();
const desktop = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  extraHTTPHeaders: { "X-Forwarded-For": "203.0.113.10" },
});
const page = await desktop.newPage();
await page.goto("http://127.0.0.1:8792/", { waitUntil: "networkidle" });
await page.waitForTimeout(500);

const initial = {
  title: await page.title(),
  header: await page.locator(".chat-title h1").innerText(),
  headerLinkHidden: await page.isHidden(".header-link"),
  stepperHidden: await page.isHidden("#stepper"),
  chipsHidden: await page.isHidden("#chips"),
  panelHidden: await page.isHidden("#intake-panel"),
};
await page.screenshot({ path: "/tmp/math-coaching-desktop.png", fullPage: true });

const before = await page.locator(".message-row-assistant").count();
await page.fill("#message-input", "미분에서 기울기의 의미를 설명해 주세요.");
await page.click("#send-button");
await page.waitForFunction(
  (count) => document.querySelectorAll(".message-row-assistant").length > count,
  before,
  { timeout: 15000 }
);
await page.waitForSelector(".typing", { state: "detached", timeout: 15000 });
await page.waitForFunction(() => document.querySelector("#turn-counter")?.textContent === "1/10");
const response = {
  reply: await page.locator(".message-row-assistant .message").last().innerText(),
  turn: await page.locator("#turn-counter").innerText(),
  panelHidden: await page.isHidden("#intake-panel"),
};

const mobile = await browser.newContext({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  extraHTTPHeaders: { "X-Forwarded-For": "203.0.113.11" },
});
const mobilePage = await mobile.newPage();
await mobilePage.goto("http://127.0.0.1:8792/", { waitUntil: "networkidle" });
await mobilePage.waitForTimeout(500);
await mobilePage.screenshot({ path: "/tmp/math-coaching-mobile.png", fullPage: true });
const mobileWidth = await mobilePage.locator("body").evaluate((element) => element.scrollWidth);

console.log(JSON.stringify({ initial, response, mobileWidth }, null, 2));
await browser.close();
