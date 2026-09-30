// No browser libraries or model credentials needed: exercise the shipped UI script.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function createUI() {
  const nodes = new Map();
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, disabled: false, textContent: "", dataset: {}, value: "",
      listeners: {}, children: [],
      querySelector: () => element("submit"),
      addEventListener(event, fn) { this.listeners[event] = fn; },
      appendChild(child) { this.children.push(child); },
      replaceChildren() { this.children = []; },
    });
    return nodes.get(id);
  }
  const fresh = { verified: false, phase: "VERIFY_ID", verification: { remaining_seconds: 0, expired: false } };
  const state = { now: 0, response: fresh, requests: [], methods: [], fail: false, confirm: true };
  const context = vm.createContext({
    document: { getElementById: element, createElement: () => element("message-node") },
    window: { addEventListener() {}, confirm: () => state.confirm },
    performance: { now: () => state.now },
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async (url, options = {}) => {
      state.requests.push(url);
      state.methods.push(options.method || "GET");
      if (state.fail) throw new Error("offline");
      return { ok: true, json: async () => state.response };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../web/static/app.js"), "utf8"), context);
  await vm.runInContext("sessionReady", context);
  return {
    state, element,
    run: code => vm.runInContext(code, context),
    render: data => { context.testPayload = data; vm.runInContext("renderConversation(testPayload)", context); },
  };
}

function verified(seconds) {
  return { verified: true, phase: "POST_PROCESS", verification: { remaining_seconds: seconds, warning_seconds: 120 },
    email_offer: { id: "offer", pending: true, can_send: true, can_skip: true } };
}

test("warning hidden until last two minutes, then counts down", async () => {
  const ui = await createUI();
  assert.equal(ui.element("verification-warning").hidden, true);
  ui.render(verified(121));
  assert.equal(ui.element("verification-warning").hidden, true);
  ui.state.now += 1000;
  ui.run("updateVerificationWarning()");
  assert.equal(ui.element("verification-warning").hidden, false);
  assert.match(ui.element("verification-warning").textContent, /2:00/);
  ui.state.now += 1000;
  ui.run("updateVerificationWarning()");
  assert.match(ui.element("verification-warning").textContent, /1:59/);
});

test("local expiry blocks send even before the timer fires", async () => {
  const ui = await createUI();
  ui.render(verified(1));
  ui.state.now += 1000;
  ui.run("updateControls()");
  assert.equal(ui.element("send-email").disabled, true);
  const requests = ui.state.requests.length;
  await ui.run('chooseEmail("send")');
  assert.equal(ui.state.requests.length, requests);
});

test("expiry refreshes server state once and keeps a visible reverify message", async () => {
  const ui = await createUI();
  ui.render(verified(1));
  ui.state.response = { verified: false, phase: "VERIFY_ID", verification: { remaining_seconds: 0, expired: true } };
  ui.state.now += 1000;
  ui.run("updateVerificationWarning()");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.element("verification-warning").hidden, false);
  assert.match(ui.element("verification-warning").textContent, /has expired/);
  assert.equal(ui.element("send-email").disabled, true);
  assert.equal(ui.element("message").disabled, false); // Reverification stays possible.
  const requests = ui.state.requests.length;
  ui.run("updateVerificationWarning(); updateVerificationWarning()");
  assert.equal(ui.state.requests.length, requests);
  ui.render(verified(900));
  assert.equal(ui.element("verification-warning").hidden, true);
  assert.equal(ui.element("send-email").disabled, false);
});

test("failed expiry refresh keeps send blocked without a one-second retry storm", async () => {
  const ui = await createUI();
  ui.render(verified(1));
  ui.state.fail = true;
  ui.state.now += 1000;
  ui.run("updateVerificationWarning()");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.element("send-email").disabled, true);
  assert.match(ui.element("service-error").textContent, /server/);
  const requests = ui.state.requests.length;
  ui.run("updateVerificationWarning(); updateControls(); updateVerificationWarning()");
  assert.equal(ui.state.requests.length, requests);
  assert.equal(ui.element("send-email").disabled, true);
});

test("restoring an expired session does not hide its warning", async () => {
  const ui = await createUI();
  ui.render({ verified: false, phase: "VERIFY_ID", verification: { expired: true, remaining_seconds: 0 } });
  assert.equal(ui.element("verification-warning").hidden, false);
  assert.match(ui.element("verification-warning").textContent, /verify again/);
});

test("clear conversation requires confirmation and resets protected UI", async () => {
  const ui = await createUI();
  ui.render(verified(120));
  ui.state.confirm = false;
  const before = ui.state.requests.length;
  await ui.element("clear-conversation").listeners.click();
  assert.equal(ui.state.requests.length, before);
  ui.state.confirm = true;
  ui.state.response = { verified: false, phase: "VERIFY_ID", reply: "Conversation cleared", verification: { expired: false } };
  await ui.element("clear-conversation").listeners.click();
  assert.equal(ui.state.methods.at(-1), "DELETE");
  assert.equal(ui.element("chat").children.length, 1);
  assert.equal(ui.element("email-choice").hidden, true);
  assert.equal(ui.element("verification-warning").hidden, true);
  assert.equal(ui.element("send-email").disabled, true);
});

test("failed deletion does not claim success or erase displayed messages", async () => {
  const ui = await createUI();
  ui.run('addMessage("user", "A question")');
  const before = ui.element("chat").children.length;
  ui.state.fail = true;
  await ui.element("clear-conversation").listeners.click();
  assert.equal(ui.element("chat").children.length, before);
  assert.match(ui.element("service-error").textContent, /Could not confirm deletion/);
  assert.equal(ui.element("clear-conversation").disabled, false);
});

test("email panel shows the preview and masked recipient only while an offer is pending", async () => {
  const ui = await createUI();
  ui.render({ ...verified(900), email_offer: { id: "offer", pending: true, can_send: true, can_skip: true,
    preview: "Claim CL-2048 (healthcare) — status: denied", recipient_masked: "m***@email.com" } });
  assert.equal(ui.element("email-preview").hidden, false);
  assert.match(ui.element("email-preview-text").textContent, /CL-2048/);
  assert.match(ui.element("email-recipient").textContent, /m\*\*\*@email\.com/);
  ui.render({ ...verified(900), email_offer: { id: "offer", pending: false, can_send: false, can_skip: false, preview: null, recipient_masked: null } });
  assert.equal(ui.element("email-preview").hidden, true);
  assert.equal(ui.element("email-recipient").hidden, true);
  assert.equal(ui.element("email-preview-text").textContent, "");
});

test("a fresh session greets the visitor and offers identity suggestions", async () => {
  const ui = await createUI();
  assert.equal(ui.element("chat").children.length, 1);
  assert.equal(ui.element("suggestions").hidden, false);
  assert.equal(ui.element("suggestions").children.length, 3);
  assert.equal(ui.element("step-VERIFY_ID").dataset.state, "active");
  assert.match(ui.element("phase-hint").textContent, /three of/);
  assert.equal(ui.element("badge-identity").dataset.state, "pending");
});

test("verification advances the steps and switches to claim question suggestions", async () => {
  const ui = await createUI();
  ui.render({ ...verified(900), claim_id: "CL-2048" });
  assert.equal(ui.element("step-VERIFY_ID").dataset.state, "done");
  assert.equal(ui.element("step-POST_PROCESS").dataset.state, "active");
  assert.equal(ui.element("badge-identity").dataset.state, "ok");
  assert.match(ui.element("badge-claim").textContent, /CL-2048/);
  assert.equal(ui.element("suggestions").children.length, 5);
  ui.render({ ...verified(900), handoff: { status: "requested", request_id: "DEMO-1" } });
  assert.equal(ui.element("suggestions").hidden, true);
});

test("a suggestion chip sends the message like the composer and clears the typing indicator", async () => {
  const ui = await createUI();
  ui.state.response = { ...verified(900), reply: "Answer", verification: { remaining_seconds: 900 } };
  const before = ui.element("chat").children.length;
  await ui.element("message-node").listeners.click();
  assert.equal(ui.state.requests.at(-1), "/api/chat");
  assert.equal(ui.element("chat").children.length, before + 2);
  assert.equal(ui.element("typing").hidden, true);
  assert.equal(ui.element("message").disabled, false);
});

test("a bound-conversation refusal shows a start-new-conversation button that clears the session", async () => {
  const ui = await createUI();
  ui.render({ verified: false, phase: "VERIFY_ID", verification: { remaining_seconds: 0 }, new_conversation_suggested: true });
  assert.equal(ui.element("new-conversation").hidden, false);
  ui.state.response = { verified: false, phase: "VERIFY_ID", reply: "Cleared", verification: { expired: false } };
  await ui.element("start-new-conversation").listeners.click();
  assert.equal(ui.state.methods.at(-1), "DELETE");
  assert.equal(ui.element("new-conversation").hidden, true);
});
