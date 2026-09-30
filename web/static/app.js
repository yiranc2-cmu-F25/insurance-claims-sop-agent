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
let emailOffer = {};
let emailDelivery = {};
let emailPollTimer = null;
let verificationDeadline = null;
let verificationWarningSeconds = 120;
let verificationExpired = false;
let expiryRefreshPending = false;
let busy = true;
let handoffStatus = "none";

function updateControls() {
  clearConversationButton.disabled = busy;
  const paused = handoffStatus === "requested";
  input.disabled = busy || paused;
  sendButton.disabled = busy || paused;
  transferButton.disabled = busy || handoffStatus !== "offered";
  returnButton.disabled = busy || !paused;
  emailYesButton.disabled = busy || paused || !emailOffer.can_send || verificationExpired
    || (verificationDeadline !== null && performance.now() >= verificationDeadline);
  emailNoButton.disabled = busy || paused || !emailOffer.can_skip;
  input.placeholder = paused ? "Bot paused — choose Return to bot to continue" : "Type a message...";
}

function showError(message = "") {
  serviceError.textContent = message;
  serviceError.hidden = !message;
}

function renderConversation(data) {
  const verification = data.verification || {};
  verificationDeadline = data.verified && verification.remaining_seconds > 0
    ? performance.now() + verification.remaining_seconds * 1000 : null;
  verificationWarningSeconds = verification.warning_seconds || 120;
  verificationExpired = Boolean(verification.expired);
  expiryRefreshPending = false;
  updateLlmStatus(data.llm_status);
  status.textContent = `Phase: ${data.phase} · Intent: ${data.intent || "unknown"} · Identity: ${data.verified ? "Verified" : "Not verified"}${data.claim_id ? " · Claim: " + data.claim_id : ""}${data.authorized_action ? " · Action: " + data.authorized_action : ""}${data.harness_status ? " · Harness: " + data.harness_status + " · Tool calls: " + data.case_tool_calls : ""}`;
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
    : "Your email choice is only confirmed by clicking a button.";
  handoffPanel.hidden = handoffStatus === "none";
  handoffPanel.dataset.state = handoffStatus;
  transferButton.hidden = requested;
  returnButton.hidden = !requested;
  document.getElementById("handoff-message").textContent = requested
    ? `Simulated transfer request created: ${handoff.request_id}. The bot is paused. No real representative is connected in this demo.`
    : `${handoff.reason_text || "A human representative can help with this request."} You can request a transfer or keep chatting with the bot.`;
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
    available: "LLM: Available",
    configured: "LLM: Configured, not checked",
    unavailable: "LLM: Unavailable",
    unknown: "LLM: Checking..."
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

clearConversationButton.addEventListener("click", async () => {
  if (busy || !window.confirm("Delete this saved conversation, identity details and case notes? This cannot be undone.")) return;
  busy = true;
  updateControls();
  clearTimeout(emailPollTimer);
  try {
    const response = await fetch("/api/conversation", { method: "DELETE" });
    if (!response.ok) throw new Error("Deletion failed");
    const data = await response.json();
    chat.replaceChildren();
    renderConversation(data);
    addMessage("assistant", data.reply);
    showError();
  } catch (_) {
    showError("Could not confirm deletion. Please refresh and try again.");
  } finally {
    busy = false;
    updateControls();
  }
});

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
    if (data.reply) addMessage("assistant", data.reply);
  } catch (error) {
    updateLlmStatus("unavailable");
    showError("Could not restore the conversation. Please reload or try again.");
  } finally {
    busy = false;
    updateControls();
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
  }
}

emailYesButton.addEventListener("click", () => chooseEmail("send"));
emailNoButton.addEventListener("click", () => chooseEmail("skip"));
window.addEventListener("focus", () => {
  if (!busy) refreshConversation().catch(() => showError("Could not refresh conversation status."));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy || handoffStatus === "requested") return;
  busy = true;
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
    addMessage("assistant", data.reply);
    renderConversation(data);
  } catch (error) {
    updateLlmStatus("unavailable");
    addMessage("assistant", "The service is unavailable. Please try again later.");
  } finally {
    busy = false;
    updateControls();
  }
});
