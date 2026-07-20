// Phase 7 리포 내재화 — 브라우저 스모크(Phase 5 원본).
// 실행: cd scripts/gui-smoke && node gui-smoke.mjs
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = 8791;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const SESSION_ID = "gui-smoke-fixed-session";
const SHOT_DIR = path.join(process.cwd(), "screenshots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

let failures = 0;
function assert(cond, label) {
  if (cond) {
    console.log(`  [PASS] ${label}`);
  } else {
    failures += 1;
    console.log(`  [FAIL] ${label}`);
  }
}

function startServer(knowledgeDir) {
  const proc = spawn(
    ".venv/bin/python",
    ["-m", "uvicorn", "app.main:app", "--port", String(PORT)],
    {
      cwd: REPO_ROOT,
      env: { ...process.env, MODEL: "fake", KNOWLEDGE_DIR: knowledgeDir },
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  let out = "";
  proc.stdout.on("data", (d) => (out += d.toString()));
  proc.stderr.on("data", (d) => (out += d.toString()));
  proc.__log = () => out;
  return proc;
}

async function waitForServer(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE_URL}/api/config`);
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await sleep(300);
  }
  return false;
}

async function stopServer(proc) {
  if (!proc) return;
  const exited = new Promise((resolve) => proc.once("exit", resolve));
  proc.kill("SIGTERM");
  const timedOut = await Promise.race([exited.then(() => false), sleep(3000).then(() => true)]);
  if (timedOut) {
    proc.kill("SIGKILL");
    await exited.catch(() => {});
  }
}

async function newPage(browser) {
  const context = await browser.newContext();
  await context.addInitScript((sid) => {
    sessionStorage.setItem("lmwiki_session_id", sid);
  }, SESSION_ID);
  return context.newPage();
}

async function waitChipsReady(page) {
  // /api/config 프로브 완료(스테퍼 노출) 대기 — 실패 시 폴백은 hidden 유지이므로
  // 스테퍼가 나타나거나 일정 시간 경과할 때까지 폴링.
  await page
    .waitForSelector("#stepper:not([hidden])", { timeout: 5000 })
    .catch(() => {});
}

async function scenarioKnowledge(browser) {
  console.log("\n=== 시나리오 1: knowledge 세트 ===");
  const page = await newPage(browser);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await waitChipsReady(page);

  const stepperVisible = await page.isVisible("#stepper");
  const chipsVisible = await page.isVisible("#chips");
  const chipCount = await page.locator("#chips .chip").count();
  const step1Active = await page
    .locator('.stepper-step[data-step="1"]')
    .evaluate((el) => el.classList.contains("active"));
  const privacyVisible = await page.isVisible(".privacy-card");
  const lockNoticeVisible = await page.isVisible(".lock-notice");

  assert(stepperVisible, "초기: 스테퍼 visible");
  assert(chipsVisible, "초기: 칩 영역 visible");
  assert(chipCount === 5, `초기: 칩 5종 (실측 ${chipCount})`);
  assert(step1Active, "초기: 스테퍼 ① active");
  assert(privacyVisible, "초기: 개인정보 카드 visible");
  assert(lockNoticeVisible, "초기: 자물쇠 문구 visible");

  await page.screenshot({ path: path.join(SHOT_DIR, "01-initial-desktop.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(SHOT_DIR, "02-initial-mobile.png") });

  await page.setViewportSize({ width: 1280, height: 800 });

  // 칩(수면) 클릭
  const beforeUserBubbles = await page.locator(".message-row-user").count();
  await page.locator('.chip:has-text("수면")').click();
  await page.waitForSelector(".typing", { state: "attached", timeout: 5000 }).catch(() => {});
  await page.waitForSelector(".typing", { state: "detached", timeout: 15000 }).catch(() => {});
  await page.waitForFunction(
    (n) => document.querySelectorAll(".message-row-user").length > n,
    beforeUserBubbles,
    { timeout: 15000 }
  );
  await page.waitForFunction(
    () => document.querySelectorAll(".message-row-assistant").length >= 2,
    { timeout: 15000 }
  );
  await page.waitForSelector("#reset-session:not([disabled])", { timeout: 15000 });
  await page.waitForSelector("#intake-panel:not([hidden])", { timeout: 15000 });

  const userBubbleHasTimestamp = await page
    .locator(".message-row-user")
    .last()
    .locator(".message-time")
    .isVisible();
  const chipsHiddenAfterClick = await page.isHidden("#chips");
  const step2Active = await page
    .locator('.stepper-step[data-step="2"]')
    .evaluate((el) => el.classList.contains("active"));
  const panelVisible = await page.isVisible("#intake-panel");
  const slotCount = await page.locator("#slot-list .slot").count();
  const contextualReplyCount = await page.locator("#contextual-replies .reply-suggestion").count();
  const resetVisible = await page.isVisible("#reset-session");
  const characterCountVisible = await page.isVisible("#character-count");

  assert(userBubbleHasTimestamp, "칩 클릭 후: 유저 말풍선 타임스탬프 렌더");
  assert(chipsHiddenAfterClick, "칩 클릭 후: 칩 행 제거(hidden)");
  assert(step2Active, "칩 클릭 후: 스테퍼 ② active (라이브 ①→② 전환)");
  assert(panelVisible, "칩 클릭 후: 사이드 패널 visible");
  assert(slotCount > 0, `칩 클릭 후: 슬롯 목록 렌더 (실측 ${slotCount})`);
  assert(contextualReplyCount > 0, `칩 클릭 후: 문맥형 빠른 답변 렌더 (실측 ${contextualReplyCount})`);
  assert(resetVisible, "칩 클릭 후: 새 대화 컨트롤 visible");
  assert(characterCountVisible, "칩 클릭 후: 글자 수 표시 visible");

  await page.screenshot({ path: path.join(SHOT_DIR, "03-after-chip-desktop.png") });

  // 2~3턴 추가 진행
  const extraTurns = ["가족과 갈등이 있어요", "요즘 도움을 좀 받고 싶어요"];
  for (const msg of extraTurns) {
    const beforeAssistant = await page.locator(".message-row-assistant").count();
    await page.fill("#message-input", msg);
    await page.click("#send-button");
    await page.waitForFunction(
      (n) => document.querySelectorAll(".message-row-assistant").length > n,
      beforeAssistant,
      { timeout: 15000 }
    );
    await page.waitForSelector("#reset-session:not([disabled])", { timeout: 15000 });
  }

  const avatarCount = await page.locator(".message-row-assistant .avatar").count();
  const allTimestamps = await page.locator(".message-time").count();
  const totalMessages = await page.locator(".messages li.message-row").count();

  assert(avatarCount >= 2, `진행 중: 아바타 렌더 (실측 ${avatarCount})`);
  assert(allTimestamps === totalMessages, `진행 중: 전 메시지 타임스탬프 렌더 (${allTimestamps}/${totalMessages})`);

  await page.screenshot({ path: path.join(SHOT_DIR, "04-after-turns-desktop.png") });

  // 스테퍼 파생 3상태 합성 단언 — window.lmwikiDeriveStep(순수 함수, DOM 무접근)
  console.log("\n=== 시나리오 2: 스테퍼 파생 3상태 합성 단언 ===");
  const cases = [
    { input: ["track", "chief_complaint"], expected: 1 },
    { input: ["chief_complaint", "expectation"], expected: 2 },
    { input: ["expectation"], expected: 3 },
    { input: [], expected: 3 },
  ];
  for (const c of cases) {
    const actual = await page.evaluate((ids) => window.lmwikiDeriveStep(ids), c.input);
    assert(
      actual === c.expected,
      `lmwikiDeriveStep(${JSON.stringify(c.input)}) === ${c.expected} (실측 ${actual})`
    );
  }

  await page.context().close();
}

async function scenarioKnowledgeAlt(browser) {
  console.log("\n=== 시나리오 3: knowledge-alt starter pack ===");
  const page = await newPage(browser);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await waitChipsReady(page);

  const stepperVisible = await page.isVisible("#stepper");
  const chipsVisible = await page.isVisible("#chips");
  const panelHiddenInitially = await page.isHidden("#intake-panel");

  assert(stepperVisible, "knowledge-alt: #stepper visible");
  assert(chipsVisible, "knowledge-alt: #chips visible");
  assert(panelHiddenInitially, "knowledge-alt: initial #intake-panel hidden");

  const beforeAssistant = await page.locator(".message-row-assistant").count();
  await page.fill("#message-input", "드립 커피를 처음 배워보고 싶어요");
  await page.click("#send-button");
  await page.waitForFunction(
    (n) => document.querySelectorAll(".message-row-assistant").length > n,
    beforeAssistant,
    { timeout: 15000 }
  );
  await page.waitForSelector("#intake-panel:not([hidden])", { timeout: 15000 });
  const slotCount = await page.locator("#slot-list .slot").count();
  assert(slotCount > 0, `knowledge-alt: slot list rendered (${slotCount})`);

  await page.screenshot({ path: path.join(SHOT_DIR, "05-knowledge-alt-desktop.png") });

  await page.context().close();
}

async function scenarioFallback(browser) {
  console.log("\n=== 시나리오 4: schema-less fallback fixture ===");
  const page = await newPage(browser);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(BASE_URL, { waitUntil: "networkidle" });
  await sleep(1000);

  const stepperHidden = await page.isHidden("#stepper");
  const chipsHidden = await page.isHidden("#chips");
  const panelHidden = await page.isHidden("#intake-panel");

  assert(stepperHidden, "fallback: #stepper hidden");
  assert(chipsHidden, "fallback: #chips hidden");
  assert(panelHidden, "fallback: #intake-panel hidden");

  const beforeAssistant = await page.locator(".message-row-assistant").count();
  await page.fill("#message-input", "원두 보관법 알려줘");
  await page.click("#send-button");
  await page.waitForFunction(
    (n) => document.querySelectorAll(".message-row-assistant").length > n,
    beforeAssistant,
    { timeout: 15000 }
  );
  const bodyText = await page.locator("body").innerText();
  assert(bodyText.includes("원두 보관법"), "fallback: document title appears");

  await page.screenshot({ path: path.join(SHOT_DIR, "06-fallback-desktop.png") });

  await page.context().close();
}

async function runScenario(browser, knowledgeDir, scenarioFn) {
  const server = startServer(knowledgeDir);
  try {
    const up = await waitForServer();
    if (!up) throw new Error(`${knowledgeDir} 서버 기동 실패\n` + server.__log());
    await scenarioFn(browser);
  } finally {
    await stopServer(server);
  }
}

async function main() {
  const browser = await chromium.launch();

  await runScenario(browser, "knowledge", scenarioKnowledge);
  await runScenario(browser, "knowledge-alt", scenarioKnowledgeAlt);
  await runScenario(browser, "tests/fixtures/knowledge-fallback", scenarioFallback);

  await browser.close();

  console.log(`\n=== 결과: ${failures === 0 ? "전부 통과" : `${failures}건 실패`} ===`);
  console.log(`스크린샷 저장 위치: ${SHOT_DIR}`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error("스모크 실행 오류:", err);
  process.exit(1);
});
