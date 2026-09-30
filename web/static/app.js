const chat = document.getElementById("chat");
const status = document.getElementById("status");
const form = document.getElementById("form");
const input = document.getElementById("message");
const sendButton = form.querySelector('button[type="submit"]');
const handoffPanel = document.getElementById("handoff");
const transferButton = document.getElementById("transfer-to-human");
const returnButton = document.getElementById("return-to-bot");
const serviceError = document.getElementById("service-error");
const clearConversationButton = document.getElementById("clear-conversation");
const emailPanel = document.getElementById("email-choice");
const emailYesButton = document.getElementById("send-email");
const emailNoButton = document.getElementById("skip-email");
const suggestions = document.getElementById("suggestions");
const typing = document.getElementById("typing");

const PHASES = ["VERIFY_ID", "RESOLVE_INTENT", "PROCESS_CASE", "POST_PROCESS"];
const PHASE_HINTS = {
  VERIFY_ID: "To protect claim details, please share any three of: full name, date of birth, phone number, email address, or the last four digits of the ID on your policy (SSN or national ID). You can also say what you are calling about right away.",
  RESOLVE_INTENT: "Tell me what you need: claim status, the denial reason, documents, the appeal deadline, payments or next steps.",
  PROCESS_CASE: "Looking up the claim and checking the answer against the record.",
  POST_PROCESS: "Ask another question, or choose below whether you would like an email summary.",
};
const WELCOME = "Hi, I'm the claims support assistant. I can help with claim status, denial reasons, required documents, appeal deadlines, payments and next steps.\n\n"
  + "Because claim details are protected, I first need to confirm your identity with any three of: full name, date of birth, phone number, email address, or the last four digits of the ID on your policy (SSN or national ID). "
  + "Feel free to tell me what you are calling about at the same time.";
const SAMPLE_CALLER = "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.";
const SUGGESTIONS = {
  unverified: [
    { label: "Use the sample caller", text: SAMPLE_CALLER },
    { label: "I'm Margaret Chen, policy POL-9921", text: "I'm Margaret Chen, the policyholder, policy POL-9921." },
    { label: "Why do you need my date of birth?", text: "Why do you need my date of birth? Is it safe to share it here?" },
  ],
  verified: [
    { label: "Claim status", text: "What is the status of my claim?" },
    { label: "Why was it denied?", text: "Why was my claim denied?" },
    { label: "Which documents do I need?", text: "What documents should I send, and what if I cannot get them?" },
    { label: "Appeal deadline", text: "What is the appeal deadline?" },
    { label: "Payments", text: "How much was paid on my claim, and what does net_fee mean?" },
  ],
};

let emailOffer = {};
let emailDelivery = {};
let emailPollTimer = null;
let verificationDeadline = null;
let verificationWarningSeconds = 120;
let verificationExpired = false;
let expiryRefreshPending = false;
let busy = true;
let sending = false;
let handoffStatus = "none";
let currentPhase = "VERIFY_ID";
let currentVerified = false;

function updateControls() {
  clearConversationButton.disabled = busy;
  document.getElementById("start-new-conversation").disabled = busy;
  const paused = handoffStatus === "requested";
  input.disabled = busy || paused;
  sendButton.disabled = busy || paused;
  transferButton.disabled = busy || handoffStatus !== "offered";
  returnButton.disabled = busy || !paused;
  emailYesButton.disabled = busy || paused || !emailOffer.can_send || verificationExpired
    || (verificationDeadline !== null && performance.now() >= verificationDeadline);
  emailNoButton.disabled = busy || paused || !emailOffer.can_skip;
  for (const chip of Array.from(suggestions.children || [])) chip.disabled = busy || paused;
  typing.hidden = !sending;
  input.placeholder = paused ? "Paused for a human transfer — choose Return to bot to continue" : "Type a message…";
}

function focusInput() {
  if (!input.disabled && typeof input.focus === "function") input.focus();
}

function showError(message = "") {
  serviceError.textContent = message;
  serviceError.hidden = !message;
}

function renderPhase(data) {
  currentPhase = PHASES.includes(data.phase) ? data.phase : "VERIFY_ID";
  currentVerified = Boolean(data.verified);
  const index = PHASES.indexOf(currentPhase);
  PHASES.forEach((phase, i) => {
    document.getElementById("step-" + phase).dataset.state = i < index ? "done" : i === index ? "active" : "todo";
  });
  document.getElementById("phase-hint").textContent = PHASE_HINTS[currentPhase];
  const identity = document.getElementById("badge-identity");
  identity.textContent = currentVerified ? "Identity verified" : "Identity not verified";
  identity.dataset.state = currentVerified ? "ok" : "pending";
  const claim = document.getElementById("badge-claim");
  claim.hidden = !data.claim_id;
  claim.textContent = data.claim_id ? "Claim " + data.claim_id : "";
}

function renderSuggestions() {
  suggestions.replaceChildren();
  const items = handoffStatus === "requested" ? [] : (currentVerified ? SUGGESTIONS.verified : SUGGESTIONS.unverified);
  suggestions.hidden = items.length === 0;
  items.forEach((item) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = item.label;
    chip.addEventListener("click", () => sendMessage(item.text));
    suggestions.appendChild(chip);
  });
}

function renderConversation(data) {
  const verification = data.verification || {};
  verificationDeadline = data.verified && verification.remaining_seconds > 0
    ? performance.now() + verification.remaining_seconds * 1000 : null;
  verificationWarningSeconds = verification.warning_seconds || 120;
  verificationExpired = Boolean(verification.expired);
  expiryRefreshPending = false;
  updateLlmStatus(data.llm_status);
  renderPhase(data);
  status.textContent = `Phase: ${data.phase} · Intent: ${data.intent || "unknown"} · Identity: ${data.verified ? "Verified" : "Not verified"}${data.claim_id ? " · Claim: " + data.claim_id : ""}${data.authorized_action ? " · Action: " + data.authorized_action : ""}${data.harness_status ? " · Harness: " + data.harness_status + " · Tool calls: " + data.case_tool_calls : ""}${data.security_status ? " · Safety: " + data.security_status : ""}`;
  const handoff = data.handoff || { status: "none" };
  handoffStatus = handoff.status;
  const requested = handoffStatus === "requested";
  emailOffer = data.email_offer || {};
  emailDelivery = data.email_delivery || {};
  const deliveryStatus = document.getElementById("email-delivery-status");
  deliveryStatus.hidden = !emailDelivery.status;
  deliveryStatus.textContent = emailDelivery.reply || "";
  emailPanel.hidden = !emailOffer.pending || requested;
  const recipient = document.getElementById("email-recipient");
  recipient.hidden = !emailOffer.recipient_masked;
  recipient.textContent = emailOffer.recipient_masked ? `Recipient on file: ${emailOffer.recipient_masked}` : "";
  document.getElementById("email-preview").hidden = !emailOffer.preview;
  document.getElementById("email-preview-text").textContent = emailOffer.preview || "";
  document.getElementById("email-choice-hint").textContent = emailOffer.pending && !emailOffer.can_send
    ? (data.security_status === "unavailable"
      ? "The safety check is unavailable. Retry your message later before sending, or skip the email."
      : data.security_status === "clarification_required"
      ? "Please answer the clarification question before sending. You can still skip the email."
      : emailDelivery.status === "pending"
      ? "The previous summary is awaiting approval. You can keep chatting or skip this new offer."
      : "Sending is not currently permitted. Check identity, authorization or safety issues; you can still skip.")
    : "Your choice is only confirmed by clicking a button; typing yes or no in the chat does not send anything.";
  document.getElementById("new-conversation").hidden = !data.new_conversation_suggested;
  handoffPanel.hidden = handoffStatus === "none";
  handoffPanel.dataset.state = handoffStatus;
  transferButton.hidden = requested;
  returnButton.hidden = !requested;
  document.getElementById("handoff-message").textContent = requested
    ? `Simulated transfer request created: ${handoff.request_id}. The assistant is paused. No real representative is connected in this demo.`
    : `${handoff.reason_text || "A human representative can help with this request."} You can request a transfer or keep chatting with the assistant.`;
  const summary = handoff.summary;
  document.getElementById("handoff-details").hidden = !requested || !summary;
  document.getElementById("handoff-summary").textContent = summary ? [
    `Verification: ${summary.verification === "verified" ? "Completed" : "Required — no claim details included"}`,
    `Workflow phase: ${summary.phase}`,
    `Customer request: ${summary.customer_request}`,
    `Transfer reason: ${summary.reason}`,
    "Completed: " + (summary.completed_steps.join("; ") || "None"),
    "Discussion: " + (summary.discussed_items.join("\n\n") || "No verified claim discussion included"),
  ].join("\n\n") : "";
  renderSuggestions();
  updateVerificationWarning();
  updateControls();
  scheduleEmailPoll();
}

function updateVerificationWarning() {
  const warning = document.getElementById("verification-warning");
  if (verificationDeadline === null && !verificationExpired) {
    warning.hidden = true;
    return;
  }
  const seconds = verificationDeadline === null ? 0
    : Math.max(0, Math.ceil((verificationDeadline - performance.now()) / 1000));
  warning.hidden = seconds > verificationWarningSeconds;
  if (seconds > 0) {
    warning.textContent = `Identity verification expires in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}. You may need to verify again to continue accessing claim details.`;
  } else {
    verificationExpired = true;
    warning.textContent = "Identity verification has expired. Please verify again before accessing claim details or sending an email.";
    emailYesButton.disabled = true;
    if (verificationDeadline !== null && !busy && !expiryRefreshPending) {
      expiryRefreshPending = true;
      busy = true;
      updateControls();
      refreshConversation().catch(() => {
        showError("Could not refresh verification status. Please retry; verification is enforced by the server.");
      }).finally(() => {
        busy = false;
        updateControls();
      });
    }
  }
}

setInterval(updateVerificationWarning, 1000);

function scheduleEmailPoll() {
  clearTimeout(emailPollTimer);
  if (!emailDelivery.can_poll) return;
  emailPollTimer = setTimeout(pollEmailApproval, 1500);
}

async function pollEmailApproval() {
  if (!emailDelivery.can_poll) return;
  if (busy) { scheduleEmailPoll(); return; }
  busy = true;
  updateControls();
  try {
    const response = await fetch("/api/email-status", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offer_id: emailDelivery.offer_id }),
    });
    if (!response.ok) {
      await refreshConversation();
      return;
    }
    renderConversation(await response.json());
  } catch (_) {
    showError("Could not check email approval. No resend was attempted; focus this window to retry.");
    clearTimeout(emailPollTimer);
  } finally {
    busy = false;
    updateControls();
  }
}

function updateLlmStatus(value) {
  const labels = {
    available: "Model: ready",
    configured: "Model: configured",
    unavailable: "Model: unavailable",
    unknown: "Model: checking…"
  };
  document.getElementById("llm-status").textContent = labels[value] || labels.unknown;
  document.getElementById("llm-status").dataset.state = value;
}

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = "message " + role;
  node.textContent = text;
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;
}

async function startNewConversation() {
  if (busy || !window.confirm("Start a new conversation? This deletes the saved conversation, identity details and case notes and cannot be undone.")) return;
  busy = true;
  updateControls();
  clearTimeout(emailPollTimer);
  try {
    const response = await fetch("/api/conversation", { method: "DELETE" });
    if (!response.ok) throw new Error("Deletion failed");
    const data = await response.json();
    chat.replaceChildren();
    renderConversation(data);
    addMessage("assistant", data.reply || WELCOME);
    showError();
  } catch (_) {
    showError("Could not confirm deletion. Please refresh and try again.");
  } finally {
    busy = false;
    updateControls();
    focusInput();
  }
}

clearConversationButton.addEventListener("click", startNewConversation);
document.getElementById("start-new-conversation").addEventListener("click", startNewConversation);

async function refreshConversation() {
  const response = await fetch("/api/conversation", { cache: "no-store" });
  if (!response.ok) throw new Error("Cannot load conversation");
  const data = await response.json();
  renderConversation(data);
  return data;
}

updateControls();
const sessionReady = (async () => {
  try {
    const response = await fetch("/api/session", { method: "POST" });
    if (!response.ok) throw new Error("Session unavailable");
    const data = await refreshConversation();
    addMessage("assistant", data.reply || WELCOME);
  } catch (error) {
    updateLlmStatus("unavailable");
    showError("Could not restore the conversation. Please reload or try again.");
  } finally {
    busy = false;
    updateControls();
    focusInput();
  }
})();

async function handoffAction(path) {
  if (busy) return;
  busy = true;
  showError();
  updateControls();
  try {
    await sessionReady;
    const response = await fetch(path, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error("Transfer state changed");
    renderConversation(data);
    if (data.reply) addMessage("assistant", data.reply);
  } catch (error) {
    showError("We could not confirm that action. Please try again.");
    // A request may have completed even if its response was lost.
    try { await refreshConversation(); } catch (_) { /* Keep existing controls. */ }
  } finally {
    busy = false;
    updateControls();
    focusInput();
  }
}

transferButton.addEventListener("click", () => handoffAction("/api/handoff"));
returnButton.addEventListener("click", () => handoffAction("/api/handoff/resume"));

async function chooseEmail(choice) {
  if (busy || handoffStatus === "requested" || !emailOffer.pending) return;
  if (choice === "send" ? !emailOffer.can_send : !emailOffer.can_skip) return;
  if (choice === "send" && (verificationExpired
    || (verificationDeadline !== null && performance.now() >= verificationDeadline))) return;
  const offerId = emailOffer.id;
  busy = true;
  showError();
  updateControls();
  try {
    await sessionReady;
    const response = await fetch("/api/email-choice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offer_id: offerId, choice }),
    });
    const data = await response.json();
    if (!response.ok) {
      showError(response.status === 409 && typeof data.detail === "string"
        ? data.detail : "We could not confirm the email choice. Please try again.");
      await refreshConversation();
      return;
    }
    renderConversation(data);
    addMessage("user", choice === "send" ? "Yes, send summary" : "No, skip");
    if (data.reply) addMessage("assistant", data.reply);
  } catch (error) {
    showError("We could not confirm the email choice. Refreshing its status; please check before retrying.");
    // A lost response does not imply failure. The server deduplicates retries.
    try {
      const data = await refreshConversation();
      if (data.email_offer?.id === offerId && !data.email_offer.pending && data.email_consent) {
        showError();
        if (data.reply) addMessage("assistant", data.reply);
      }
    } catch (_) { /* Keep the same offer ID so a retry cannot send twice. */ }
  } finally {
    busy = false;
    updateControls();
    focusInput();
  }
}

emailYesButton.addEventListener("click", () => chooseEmail("send"));
emailNoButton.addEventListener("click", () => chooseEmail("skip"));
window.addEventListener("focus", () => {
  if (!busy) refreshConversation().catch(() => showError("Could not refresh conversation status."));
});

async function sendMessage(text) {
  const message = (text || "").trim();
  if (!message || busy || handoffStatus === "requested") return;
  busy = true;
  sending = true;
  showError();
  updateControls();
  try {
    await sessionReady;
    addMessage("user", message);
    input.value = "";
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message })
    });
    const data = await response.json();
    if (!data.reply) throw new Error("Service unavailable");
    sending = false;
    addMessage("assistant", data.reply);
    renderConversation(data);
  } catch (error) {
    sending = false;
    updateLlmStatus("unavailable");
    addMessage("assistant", "The service is unavailable right now. Please try again in a moment.");
  } finally {
    busy = false;
    sending = false;
    updateControls();
    focusInput();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});
