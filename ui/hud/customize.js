import { ACCESSORIES, SHELL_COLORS } from "/ui/core/archetype.js";
import { setAvatarStyle } from "/ui/platform/api.js";
import { frozen } from "/ui/platform/clock.js";
import { createSoundNotifications, setSound, soundOn } from "/ui/platform/sound.js";

const VIEW_KEY = "aioffice.iso.camera";
const VIEWS = Object.freeze({ compact: { az: 45, el: 50, zoom: 1.05 },
  standard: { az: 45, el: 40, zoom: 1 }, stream: { az: 38, el: 36, zoom: .94 } });

export function init({ shell, T, scene, DEMO, stream, refresh, showToast,
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
  // R91: 本人指摘「統一で全部変わるぐらいだったら中途半端」への回答。
  // ①入口はシートの🎨（名札クリックは会話へ）②既定は「この1体だけ」③帽子に加えて殻の色。
  let scope = "session";
  const openAccessories = (agent) => {
    const editable = !DEMO && !stream.enabled && !agent.external && /^[0-9a-f]{12}$/.test(agent.id);
    // 見出し＋操作の1行。設定モーダルの seg と同じ .mledform を使い、新しいCSSを増やさない。
    const row = (title, node) => {
      const box = mEl("div", "mledform");
      box.append(mEl("span", "mledname", title), node);
      return box;
    };
    const paint = () => {
      modal.replaceChildren(mEl("b", "mtitle", `${agent.name} · ${T("avatar_title")}`));

      const scopes = mEl("div", "accessory-choices tight");
      for (const [value, label] of [["session", T("avatar_scope_session")],
        ["project", T("avatar_scope_project")]]) {
        const b = mEl("button", `mkeybtn${scope === value ? " on" : ""}`, label);
        b.type = "button"; b.disabled = !editable;
        b.setAttribute("aria-pressed", String(scope === value));
        b.addEventListener("click", () => { scope = value; paint(); });
        scopes.append(b);
      }
      modal.append(row(T("avatar_scope"), scopes));
      modal.append(mEl("p", "mnote", T("avatar_scope_note")));

      const save = async (style, buttons) => {
        for (const b of buttons) b.disabled = true;
        try { await setAvatarStyle(agent.id, scope, style); closeModal(); refresh(); }
        catch (err) { showToast(err.message, false); for (const b of buttons) b.disabled = false; }
      };

      const choices = mEl("div", "accessory-choices");
      for (const value of [null, ...Object.keys(ACCESSORIES)]) {
        const selected = value === null ? agent.arch?.kind === "generic"
          : ACCESSORIES[value] === agent.arch?.kind || value === "phones" && agent.arch?.kind === "audio";
        const b = mEl("button", `mkeybtn${selected ? " on" : ""}`, T(`arch_${value || "none"}`));
        b.type = "button"; b.disabled = !editable;
        b.setAttribute("aria-pressed", String(selected));
        b.addEventListener("click", () => save({ arch: value }, choices.children));
        choices.append(b);
      }
      modal.append(row(T("arch_title"), choices));

      const swatches = mEl("div", "color-choices");
      for (const value of [null, ...Object.keys(SHELL_COLORS)]) {
        const selected = value === null ? !agent.arch?.color : agent.arch?.color === value;
        const b = mEl("button", `swatch${selected ? " on" : ""}`);
        b.type = "button"; b.disabled = !editable;
        b.title = T(`color_${value || "default"}`);
        b.setAttribute("aria-label", b.title);
        b.setAttribute("aria-pressed", String(selected));
        if (value) b.style.setProperty("--sw", SHELL_COLORS[value].swatch);
        else b.classList.add("swatch-none");
        b.addEventListener("click", () => save({ color: value }, swatches.children));
        swatches.append(b);
      }
      modal.append(row(T("avatar_color"), swatches));

      if (!editable) modal.append(mEl("p", "mnote", T("arch_readonly")));
      // 会話へ戻る導線は置かない＝この画面はシートの🎨から来るので、閉じれば会話が残っている
    };
    scope = "session";
    paint();
    openModal();
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
