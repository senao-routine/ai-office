import { hireSession, pickProjectFolder, projectIdForPath } from "/ui/platform/api.js";
import { frozen } from "/ui/platform/clock.js";
import { agoStr } from "/ui/core/world.js";

/** Three-step local launch sheet. The server owns eligibility and concurrency. */
export function init({ shell, T, lang, getWorld, DEMO, arrivals, refresh, showToast,
  modals: { modal, openModal, closeModal, mEl } }) {
  if (frozen || DEMO) return { dispose() {} };
  const button = mEl("button", "abtn", T("btn_hire"));
  button.type = "button"; button.id = "btn-hire";
  shell.querySelector("#btn-newproj").after(button);
  let launching = false;
  let disposed = false;
  const open = () => {
    const form = mEl("form", "hire-form");
    form.id = "hire-form";
    const step = (title) => {
      const field = mEl("fieldset", "hire-step");
      field.append(mEl("legend", "", T(title))); form.append(field);
      return field;
    };
    const where = step("hire_where");
    const projects = mEl("select", "minput");
    projects.id = "hire-project"; projects.setAttribute("aria-label", T("hire_where"));
    projects.append(mEl("option", "", T("hire_choose")));
    projects.firstElementChild.value = "";
    for (const p of getWorld()?.launchable || []) {
      const option = mEl("option", "", `${p.name} · ${agoStr(p.ageSec, lang())}`);
      option.value = p.projectId; projects.append(option);
    }
    const pick = mEl("button", "abtn", T("hire_pick"));
    pick.id = "hire-pick"; pick.type = "button";
    const pickedNote = mEl("p", "mnote"); pickedNote.hidden = true;
    where.append(projects, pick, pickedNote);
    const what = step("hire_what");
    const prompt = mEl("input", "minput");
    prompt.type = "text"; prompt.id = "hire-prompt"; prompt.autocomplete = "off";
    prompt.placeholder = T("hire_prompt"); prompt.setAttribute("aria-label", T("hire_prompt"));
    // Count Unicode code points, matching Python's 4000-character server limit.
    const count = mEl("p", "mnote"); count.id = "hire-count";
    prompt.setAttribute("aria-describedby", count.id);
    const isolation = mEl("label", "mopt");
    const worktree = mEl("input"); worktree.type = "checkbox"; worktree.id = "hire-worktree";
    worktree.setAttribute("role", "switch");
    isolation.append(worktree, document.createTextNode(T("hire_worktree")));
    what.append(prompt, count, isolation);
    const who = step("hire_who");
    const provider = mEl("select", "minput");
    provider.id = "hire-provider"; provider.setAttribute("aria-label", T("hire_who"));
    for (const value of ["claude", "codex"]) {
      const option = mEl("option", "", T(`hire_${value}`)); option.value = value; provider.append(option);
    }
    const terminal = mEl("p", "mnote", T("hire_terminal_note")); terminal.hidden = true;
    const command = mEl("code", "hire-command", T("hire_terminal_cmd")); command.hidden = true;
    who.append(provider, terminal, command);
    const status = mEl("p", "mnote"); status.id = "hire-status"; status.setAttribute("role", "status");
    const go = mEl("button", "mgo"); go.id = "mgo-hire"; go.type = "submit";
    form.append(status, go);
    let picking = false;
    const normalized = () => prompt.value.trim().replace(/\s+/gu, " ");
    const sync = () => {
      button.disabled = launching;
      const codex = provider.value === "codex";
      const size = [...normalized()].length;
      where.disabled = launching || picking || codex;
      what.disabled = launching || codex;
      provider.disabled = launching || picking;
      terminal.hidden = command.hidden = !codex;
      count.textContent = T("hire_count", size);
      count.classList.toggle("merr", size > 4000);
      go.textContent = T(launching ? "hire_starting" : codex ? "hire_terminal_copy" : "hire_go");
      go.disabled = launching || picking || (!codex &&
        (!/^[0-9a-f]{12}$/.test(projects.value) || size < 1 || size > 4000 || normalized().includes("\0")));
    };
    pick.addEventListener("click", async () => {
      if (picking || launching) return;
      picking = true; status.textContent = T("np_opening"); sync();
      try {
        const result = await pickProjectFolder();
        if (!result?.path || result.ok === false) throw new Error();
        const id = await projectIdForPath(result.path);
        if (disposed || !modal.contains(form)) return;
        if (![...projects.options].some((o) => o.value === id)) {
          const option = mEl("option", "", result.suggest || T("hire_picked"));
          option.value = id; projects.append(option);
        }
        projects.value = id;
        pickedNote.textContent = T("hire_pick_note"); pickedNote.hidden = false;
        status.textContent = "";
      } catch {
        if (modal.contains(form)) status.textContent = T("hire_pick_failed");
      } finally { picking = false; sync(); }
    });
    for (const input of [projects, prompt, provider]) input.addEventListener("input", sync);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); sync();
      if (go.disabled) return;
      if (provider.value === "codex") {
        // There is no Terminal-only API: projects/launch also starts Claude.
        try {
          await navigator.clipboard.writeText(T("hire_terminal_cmd"));
          status.textContent = T("hire_terminal_copied");
        } catch { status.textContent = T("hire_terminal_note"); }
        return;
      }
      launching = true; status.textContent = T("hire_starting"); sync();
      try {
        const result = await hireSession(projects.value, normalized(), worktree.checked);
        if (!result?.ok) throw new Error();
        arrivals.hired(result.bgId);
        if (!disposed) {
          if (modal.contains(form)) closeModal();
          showToast(T("hire_started")); refresh();
        }
      } catch {
        if (!disposed) {
          if (modal.contains(form)) status.textContent = T("hire_failed");
          else showToast(T("hire_failed"), false);
        }
      } finally { launching = false; sync(); }
    });
    modal.replaceChildren(mEl("b", "mtitle", T("btn_hire")), form);
    sync(); openModal(); projects.focus();
  };
  button.addEventListener("click", open);
  return { dispose() { disposed = true; button.removeEventListener("click", open); button.remove(); } };
}
