import { ACCESSORIES } from "/ui/core/archetype.js";
import { setProjectArch } from "/ui/platform/api.js";
import { frozen } from "/ui/platform/clock.js";
import { createSoundNotifications, setSound, soundOn } from "/ui/platform/sound.js";

const VIEW_KEY = "aioffice.iso.camera";
const VIEWS = Object.freeze({ compact: { az: 45, el: 50, zoom: 1.05 },
  standard: { az: 45, el: 40, zoom: 1 }, stream: { az: 38, el: 36, zoom: .94 } });

export function init({ shell, T, scene, DEMO, stream, refresh, showToast, openCompose,
  modals: { modal, mEl, openModal, closeModal } }) {
  let view = "standard", consent = null;
  if (!frozen) {
    try { const saved = localStorage.getItem(VIEW_KEY); if (Object.hasOwn(VIEWS, saved)) view = saved; } catch { /* Optional storage. */ }
    const q = new URLSearchParams(location.search);
    if (view !== "standard" && !q.has("az") && !q.has("el")) scene.setCameraView?.(VIEWS[view]);
  }
  const sounds = createSoundNotifications({ isolated: DEMO || stream.enabled, ask: () => {
    consent = mEl("div", "toast sound-consent");
    consent.setAttribute("role", "status");
    const message = mEl("div", "tmsg show");
    message.append(mEl("span", "", T("sound_ask")));
    for (const [enabled, label] of [[true, "sound_yes"], [false, "sound_no"]]) {
      const button = mEl("button", "mkeybtn", T(label)); button.type = "button";
      button.addEventListener("click", () => {
        setSound(enabled); consent.remove(); consent = null;
        shell.querySelector("#sheetsnd").textContent = soundOn() ? "🔈" : "🔇";
      });
      message.append(button);
    }
    consent.append(message); shell.querySelector("#stage").append(consent);
  } });
  const openAccessories = (agent) => {
    modal.replaceChildren(mEl("b", "mtitle", `${agent.name} · ${T("arch_title")}`));
    const editable = !DEMO && !stream.enabled && !agent.external && /^[0-9a-f]{12}$/.test(agent.id);
    const choices = mEl("div", "accessory-choices");
    for (const value of [null, ...Object.keys(ACCESSORIES)]) {
      const selected = value === null ? agent.arch?.kind === "generic" : ACCESSORIES[value] === agent.arch?.kind
        || value === "phones" && agent.arch?.kind === "audio";
      const b = mEl("button", `mkeybtn${selected ? " on" : ""}`, T(`arch_${value || "none"}`));
      b.type = "button"; b.disabled = !editable;
      b.setAttribute("aria-pressed", String(selected));
      b.addEventListener("click", async () => {
        for (const button of choices.children) button.disabled = true;
        try { await setProjectArch(agent.id, value); closeModal(); refresh(); }
        catch (err) { showToast(err.message, false); for (const button of choices.children) button.disabled = false; }
      });
      choices.append(b);
    }
    modal.append(choices);
    if (!editable) modal.append(mEl("p", "mnote", T("arch_readonly")));
    const chat = mEl("button", "mgo", T("arch_chat")); chat.type = "button";
    chat.addEventListener("click", () => { closeModal(); openCompose(agent); });
    modal.append(chat); openModal();
  };
  const renderSettings = (seg, repaint) => {
    seg(T("snd_title"), [["off", T("sound_off")], ["on", T("sound_on")]], soundOn() ? "on" : "off", (value) => {
      setSound(value === "on");
      shell.querySelector("#sheetsnd").textContent = soundOn() ? "🔈" : "🔇";
      repaint();
    });
    seg(T("camera_title"), Object.keys(VIEWS).map((key) => [key, T(`camera_${key}`)]), view, (value) => {
      view = value;
      if (!frozen) {
        try { localStorage.setItem(VIEW_KEY, value); } catch { /* Session selection still works. */ }
      }
      scene.setCameraView?.(VIEWS[value]); repaint();
    });
  };
  return { openAccessories, renderSettings, update: sounds.update,
    dispose() { sounds.dispose(); consent?.remove(); } };
}
