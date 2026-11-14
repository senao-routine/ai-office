// R128 #4: ⚙ 設定の「Bot」の節＝出勤の口の Bot の一覧・追加・削除・消音・登録コマンドのコピー（契約の正本= docs/door.md §10）。
// 様式に依らない部品: ui/hud/admin.js の設定（app・cockpit・iso・pixel の ⚙）が呼ぶ。サーバーは GET /api/door/bots と
// Origin 必須の POST /api/door/bot/{add,remove,mute}＝Mac の前の人だけ（中継・MCP・Bot の道具には道が無い）。
// 掟: 名前・役・id はサーバーから来る信用しない文字＝必ず textContent（mEl の第 3 引数・innerHTML は使わない）。
//     登録コマンドはサーバーが valid_id を通った id で組んだ 1 行をそのまま写す（ここで組み立てない＝シェルの特殊文字が混ざらない）。
//     削除は取り消せない（状態と記録が消える）＝2 回押し（1 回目で 3 秒だけ「もう一度押すと削除」・admin の「外す」「まとめて失効」と同じ）。
//     要素は参照で持つ（querySelector の文字で探さない＝tests/hud_ids.test.mjs の「様式の shell が持つ契約」に数えない）。
import { agoStr } from "/ui/core/world.js";
import { doorBotAdd, doorBotMute, doorBotRemove, doorBots } from "/ui/platform/api.js";

const ARM_MS = 3000;       // 削除の 2 回押しの窓（admin の「外す」と同じ 3 秒）
const COPIED_MS = 1800;    // 「コピーしました」を見せる長さ（初回の案内のコピーと同じ）

/**
 * host に Bot の節を足す。ctx: T（文言）・lang()・mEl（textContent で作る要素工場）・showToast(text, ok)・demo（サーバーが無い）・
 * separate（節の見出しと上の罫を自分で出す＝cockpit 以外。cockpit は admin の group() が ┤ Bot ├ の節を作る）・
 * copy（クリップボードへ書く関数・既定は navigator.clipboard）。返り値: { box, reload }。
 */
export function renderBotPanel(host, { T, lang, mEl, showToast, demo = false, separate = true,
  copy = (text) => navigator.clipboard.writeText(text) }) {
  const box = mEl("div", separate ? "mbots mbots-sep" : "mbots");
  if (separate) box.append(mEl("b", "msub", T("bot_title")));
  const lead = mEl("p", "mnote", T(demo ? "demo_server_only" : "bot_loading"));
  const list = mEl("div", "mdevices mbotlist");
  const notes = mEl("div", "mbotnotes");
  box.append(lead, list, notes);
  host.append(box);
  if (demo) return { box, reload: () => Promise.resolve() };   // デモはサーバーが無い＝一覧も欄も出さない

  let gen = 0;               // 古い読み込みの結果で描き直さない（操作の直後の読み直しと重なったとき）
  const live = () => box.isConnected;
  const label = (b) => String(b.name || b.bot || "");

  const muteButton = (b) => {
    const btn = mEl("button", "mbtn mbotmute", T(b.muted ? "bot_unmute" : "bot_mute"));
    btn.type = "button";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await doorBotMute(b.bot, !b.muted);
        showToast(T(b.muted ? "bot_unmute_done" : "bot_mute_done", label(b)));
        await reload();
      } catch (err) {
        btn.disabled = false;
        showToast(err.message, false);
      }
    });
    return btn;
  };

  const copyButton = (b, cmd) => {
    const btn = mEl("button", "mbtn mbotcopy", T("bot_copy"));
    btn.type = "button";
    btn.disabled = !b.mcp;
    let timer = 0;
    btn.addEventListener("click", async () => {
      try {
        await copy(String(b.mcp));
        btn.textContent = T("bot_copied");
        clearTimeout(timer);
        timer = setTimeout(() => { if (btn.isConnected) btn.textContent = T("bot_copy"); }, COPIED_MS);
      } catch {
        // クリップボードが使えない（権限・安全でない文脈）＝行に見えているコマンドを選んでおく（⌘C で写せる）
        try { getSelection()?.selectAllChildren(cmd); } catch { /* 選べなくてもコマンドは見えている */ }
        showToast(T("bot_copy_failed"), false);
      }
    });
    return btn;
  };

  const removeButton = (b) => {
    const btn = mEl("button", "mrevoke mbotremove", T("bot_remove"));
    btn.type = "button";
    let armed = 0;
    btn.addEventListener("click", async () => {
      if (!armed) {
        armed = setTimeout(() => { armed = 0; if (btn.isConnected) btn.textContent = T("bot_remove"); }, ARM_MS);
        btn.textContent = T("bot_remove_arm");
        return;
      }
      clearTimeout(armed);
      armed = 0;
      btn.disabled = true;
      try {
        await doorBotRemove(b.bot);
        showToast(T("bot_removed", label(b)));
        await reload();
      } catch (err) {
        btn.disabled = false;
        btn.textContent = T("bot_remove");
        showToast(err.message, false);
      }
    });
    return btn;
  };

  // 1 体 1 行（端末の一覧 .mdev と同じ 3 段＋登録コマンド）: 名前・役・消音中／id・最後に動いた時刻・相談・未読／コマンド／操作
  // 操作の並びは 消音 → コピー → 削除（取り消せない操作を最後に＝押し間違えない並び）
  const row = (b) => {
    const r = mEl("div", "mdev mbot");
    r.dataset.bot = String(b.bot || "");
    const top = mEl("div", "mdevtop");
    top.append(mEl("b", "mdevname", label(b)), mEl("span", "devmeta", String(b.role || "")));
    if (b.muted) top.append(mEl("span", "devmeta mbotmuted", T("bot_muted")));
    const facts = [String(b.bot || ""),
      Number.isFinite(b.age) ? T("bot_last", agoStr(b.age, lang())) : T("bot_never")];
    if (b.ask) facts.push(T("bot_asking"));
    if (Number(b.unread) > 0) facts.push(T("bot_unread", Number(b.unread)));
    const cmd = mEl("code", "mbotcmd", String(b.mcp || ""));
    cmd.hidden = !b.mcp;
    const ops = mEl("div", "mdevops");
    ops.append(muteButton(b), copyButton(b, cmd), removeButton(b));
    r.append(top, mEl("div", "mdevsub", facts.join(" · ")), cmd, ops);
    return r;
  };

  const paint = (data) => {
    const bots = Array.isArray(data?.bots) ? data.bots : [];
    const broken = data?.ledger === false;
    lead.className = broken ? "mnote merr" : "mnote";
    lead.textContent = T(broken ? "bot_ledger_broken" : bots.length ? "bot_count" : "bot_empty", bots.length, data?.max);
    list.replaceChildren(...bots.map(row));
    notes.replaceChildren(...(bots.length ? [mEl("p", "mnote", T("bot_mute_note")), mEl("p", "mnote", T("bot_cmd_note"))] : []));
  };

  const reload = async () => {
    const mine = ++gen;
    try {
      const data = await doorBots();
      if (mine === gen && live()) paint(data);
    } catch (err) {
      if (mine !== gen || !live()) return;
      lead.className = "mnote merr";
      lead.textContent = T("bot_load_failed", err.message);
    }
  };

  // 追加の欄（id・名前・役）。名前と役は空なら送らない（サーバーの既定＝名前は id・役は Bot）。同じ id は名前と役の直し
  const form = mEl("form", "mbotform");
  const field = (key, max) => {
    const input = mEl("input", "minput mbotin");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("autocapitalize", "off");
    input.maxLength = max;
    input.placeholder = T(key);
    input.setAttribute("aria-label", T(key));
    return input;
  };
  const idIn = field("bot_id_ph", 32), nameIn = field("bot_name_ph", 24), roleIn = field("bot_role_ph", 16);
  const add = mEl("button", "mgo mbotadd", T("bot_add"));
  add.type = "submit";
  form.append(idIn, nameIn, roleIn, add);
  const formErr = mEl("p", "mnote merr mboterr");
  formErr.hidden = true;
  formErr.setAttribute("role", "alert");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (add.disabled) return;
    const id = idIn.value.trim(), name = nameIn.value.trim(), role = roleIn.value.trim();
    formErr.hidden = true;
    add.disabled = true;
    try {
      const res = await doorBotAdd(id, name || null, role || null);
      showToast(T(res?.updated ? "bot_updated" : "bot_added", String(res?.name || id)));
      idIn.value = nameIn.value = roleIn.value = "";
      await reload();
    } catch (err) {
      // 断りの理由はサーバー（口）の文をそのまま（形・上限・台帳・64 体）＝欄の下に残す（トーストは消える）
      formErr.textContent = err.message;
      formErr.hidden = false;
    } finally {
      add.disabled = false;
    }
  });
  box.append(form, mEl("p", "mnote", T("bot_add_note")), formErr);

  reload();
  return { box, reload };
}
