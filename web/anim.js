// Landing-page port of cli/src/pokbot_cli/anim.py: the BOT Arena splash, the arena and the showdown hand,
// drawn as a character grid into <pre id="arena-anim">. Keep the scenes in step with anim.py.
// Static (final frame) under prefers-reduced-motion; paused while off screen or in a hidden tab.
(() => {
  const el = document.getElementById("arena-anim");
  if (!el) return;
  const W = 60;
  const SUITS = "♠♥♦♣";
  const WIDE = /[♠♥♦♣●▶▌✓▰▱]/;
  const SPARK_STYLES = ["gold", "red", "win", "shine"];
  const TAGLINE = "submit your poker bot · it plays every 30 minutes";
  const FONT = {
    B: ["████ ", "█   █", "████ ", "█   █", "████ "],
    O: [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    T: ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    A: [" ███ ", "█   █", "█████", "█   █", "█   █"],
    R: ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    E: ["█████", "█    ", "████ ", "█    ", "█████"],
    N: ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    " ": Array(5).fill("   "),
  };

  function rng(seed) { // mulberry32
    return () => {
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const pick = (r, s) => s[Math.floor(r() * s.length)];
  const suitStyle = (label) => (/[♥♦]$/.test(label) ? "red" : "card");

  class Grid {
    constructor(h) { this.h = h; this.cells = Array.from({ length: h }, () => Array.from({ length: W }, () => [" ", ""])); }
    put(x, y, text, style = "") {
      [...text].forEach((ch, i) => { if (x + i >= 0 && x + i < W && y >= 0 && y < this.h) this.cells[y][x + i] = [ch, style]; });
    }
    center(y, text, style = "") { this.put(Math.floor((W - [...text].length) / 2), y, text, style); }
    html() {
      return this.cells.map((row) => {
        let out = "", cur = null, run = "";
        const flush = () => { if (run) out += cur ? `<span class="a-${cur}">${run}</span>` : run; run = ""; };
        // Suits and symbols may come from a fallback font: pin each to one cell so columns line up.
        for (const [ch, style] of row) { if (style !== cur) { flush(); cur = style; } run += WIDE.test(ch) ? `<i>${ch}</i>` : ch; }
        flush();
        return out;
      }).join("\n");
    }
  }

  // --- splash ---
  function* splash(seed = 7) {
    const r = rng(seed);
    const rows = [0, 1, 2, 3, 4].map((i) => [..."BOT ARENA"].map((c) => FONT[c][i] + " ").join("").trimEnd());
    const left = Math.floor((W - rows[0].length) / 2);
    const cells = [];
    rows.forEach((row, y) => [...row].forEach((ch, x) => { if (ch !== " ") cells.push([x, y, Math.floor(x * 0.55) + Math.floor(r() * 8)]); }));
    const settle = Math.max(...cells.map((c) => c[2])) + 1;
    const total = settle + 14 + Math.floor(TAGLINE.length / 2) + 14;
    for (let f = 0; f < total; f++) {
      const g = new Grid(9);
      const shine = f - settle;
      for (const [x, y, at] of cells) {
        if (f < at - 6) continue;
        if (f < at) { const ch = pick(r, SUITS); g.put(left + x, y + 1, ch, /[♥♦]/.test(ch) ? "red" : "muted"); }
        else g.put(left + x, y + 1, "█", shine * 4 - x >= 0 && shine * 4 - x < 6 ? "shine" : "gold");
      }
      const typed = Math.max(0, (f - settle - 10) * 2);
      if (typed) g.center(7, TAGLINE.slice(0, typed) + (typed < TAGLINE.length && f % 2 ? "▌" : " "), "ink");
      if (f >= settle) {
        const suits = [...SUITS].join("  ");
        g.center(8, suits, "dim");
        const k = Math.floor(f / 3) % 4;
        g.put(Math.floor((W - suits.length) / 2) + k * 3, 8, SUITS[k], /[♥♦]/.test(SUITS[k]) ? "red" : "gold");
      }
      yield g;
    }
  }

  // --- arena: six tables race through 10 sets while the set leader changes and settles ---
  const SET_WINNERS = ["l5-shark", "tag", "l5-shark", "pairs", "pairs", "l5-shark", "pairs", "pairs", "tag", "pairs"];
  const RACERS = ["pairs", "l5-shark", "tag"];
  const DECK = [..."AKQJT98765432"].flatMap((r) => [...SUITS].map((s) => r + s));
  const fmt = (n) => n.toLocaleString("en-US");

  // bots, hands: live numbers from /api/leaderboard; null shows only the per-table rules.
  function* arena(bots = null, hands = null, seed = 7) {
    const r = rng(seed);
    const speeds = [0, 1, 2, 3, 4, 5].map((i) => 104 + 7 * i);
    let header = "6 tables · 10 sets × 1,000 hands each";
    if (bots) header = `${fmt(bots)} bots entered · ` + header;
    for (let f = 0; f < 132; f++) {
      const g = new Grid(13);
      g.center(0, header, "muted");
      speeds.forEach((speed, i) => {
        const x = 1 + (i % 3) * 20, y = 1 + Math.floor(i / 3) * 3;
        const played = Math.min(10000, f * speed), done = played === 10000;
        const border = done ? "win" : "back";
        g.put(x, y, "╭" + ` table ${i + 1} `.padEnd(16, "─") + "╮", border);
        g.put(x, y + 1, "│", border);
        if (done) g.put(x + 2, y + 1, "✓ done", "win");
        else for (let k = 0; k < 2; k++) { const card = pick(r, DECK); g.put(x + 2 + k * 3, y + 1, card, suitStyle(card)); }
        g.put(x + 10, y + 1, fmt(played).padStart(6), done ? "muted" : "ink");
        g.put(x + 17, y + 1, "│", border);
        const sets = Math.floor(played / 1000);
        g.put(x, y + 2, "╰", border);
        for (let k = 0; k < 10; k++) g.put(x + 1 + k, y + 2, k < sets ? "▰" : "▱", k < sets ? "gold" : "dim");
        g.put(x + 11, y + 2, "──────╯", border);
      });
      const t = Math.min(1, f / 100), ease = 1 - (1 - t) ** 3;
      if (hands) g.center(8, `${fmt(Math.round(hands * ease))} hands played`, t === 1 && f % 8 < 4 ? "shine" : "gold");
      else g.center(8, `${fmt(Math.round(10000 * ease))} hands at every table`, "gold");
      const decided = Math.min(10, Math.floor(f / 10));
      const wins = Object.fromEntries(RACERS.map((n) => [n, SET_WINNERS.slice(0, decided).filter((w) => w === n).length]));
      const order = [...RACERS].sort((a, b) => wins[b] - wins[a] || RACERS.indexOf(a) - RACERS.indexOf(b));
      order.forEach((name, row) => {
        const lead = row === 0 && wins[name] > 0;
        g.put(4, 9 + row, (lead ? "▶ " : "  ") + name.padEnd(9), lead ? "gold" : "ink");
        g.put(16, 9 + row, "█".repeat(wins[name] * 3), lead ? "gold" : "back");
        g.put(48, 9 + row, `${wins[name]} set${wins[name] === 1 ? "" : "s"}`, "muted");
      });
      if (decided < 4) g.put(2, 12, "early sets are luck: anyone can lead", "muted");
      else if (decided < 10) g.put(2, 12, `set ${decided} of 10 · the luck is washing out`, "ink");
      else g.put(2, 12, "10,000 hands later, the real OG is on top", "gold");
      yield g;
    }
  }

  // --- showdown: pairs (K♠ K♦) vs l5-shark (A♥ Q♥) ---
  const BOARD = ["K♣", "7♦", "2♠", "A♠", "9♥"];
  const SEATS = { pairs: 8, "l5-shark": 10 };
  const HOLES = { pairs: ["K♠", "K♦"], "l5-shark": ["A♥", "Q♥"] };
  const CARD_X = [13, 20, 27, 34, 41];
  const HEADER = "pokbot play pairs --vs l5-shark · hand 642 of 1,000";

  class Table {
    constructor(seed = 7) {
      this.r = rng(seed);
      this.board = Array(5).fill("slot");
      this.shown = { pairs: true, "l5-shark": false };
      this.flip = { pairs: 0, "l5-shark": 0 };
      this.stack = { pairs: 200, "l5-shark": 200 };
      this.acts = { pairs: "", "l5-shark": "" };
      this.pot = 0; this.potShown = 0;
      this.ticker = ["muted", "heads-up · blinds 1/2 · 5 ms per decision"];
      this.chips = []; this.sparks = []; this.winner = "";
    }
    card(g, x, state, label, hot) {
      const rows = { slot: ["╭┈┈┈┈╮", "┊    ┊", "╰┈┈┈┈╯"], back: ["╭────╮", "│░░░░│", "╰────╯"], flip: [" ╭──╮ ", " │▒▒│ ", " ╰──╯ "] }[state];
      if (rows) { rows.forEach((row, dy) => g.put(x, 2 + dy, row, state === "slot" ? "dim" : "back")); return; }
      const border = hot ? "win" : "muted";
      g.put(x, 2, "╭────╮", border);
      g.put(x, 3, "│", border); g.put(x + 1, 3, label.padEnd(4), hot ? "win" : suitStyle(label)); g.put(x + 5, 3, "│", border);
      g.put(x, 4, "╰────╯", border);
    }
    draw() {
      const g = new Grid(13);
      for (const [x, y, , style, ch] of this.sparks) g.put(x, y, ch, style);
      g.put(2, 0, HEADER, "muted");
      CARD_X.forEach((x, i) => this.card(g, x, this.board[i], BOARD[i], !!this.winner && BOARD[i][0] === "K"));
      for (const [name, y] of Object.entries(SEATS)) {
        const mine = this.winner === name;
        g.put(2, y, (mine ? "▶ " : "  ") + name, mine ? "gold" : "ink");
        HOLES[name].forEach((label, k) => {
          const x = 15 + k * 5;
          if (this.flip[name] === 1) g.put(x, y, "[▒▒]", "back");
          else if (this.shown[name]) {
            g.put(x, y, "[", "muted"); g.put(x + 1, y, label, this.winner && mine ? "win" : suitStyle(label)); g.put(x + 3, y, "]", "muted");
          } else g.put(x, y, "[░░]", "back");
        });
        g.put(27, y, `stack ${String(this.stack[name]).padStart(3)}`, "muted");
        g.put(40, y, this.acts[name], mine ? "gold" : "ink");
      }
      for (const [x, y] of this.chips) g.put(Math.round(x), Math.round(y), "●", "gold");
      g.center(6, `pot ${Math.round(this.potShown)}`, "gold");
      g.put(2, 12, this.ticker[1], this.ticker[0]);
      return g;
    }
    tick() {
      this.potShown += (this.pot - this.potShown) * 0.35;
      if (Math.abs(this.pot - this.potShown) < 0.5) this.potShown = this.pot;
      for (const c of this.chips) {
        const [x0, y0, tx, ty] = c;
        const t = Math.min(1, c[4] + 0.2);
        const ease = 1 - (1 - Math.max(0, t)) ** 2;
        c.splice(0, 5, x0 + (tx - x0) * ease, y0 + (ty - y0) * ease, tx, ty, t);
      }
      this.chips = this.chips.filter((c) => c[4] < 1);
      if (this.winner) {
        for (let i = 0; i < 3; i++) {
          const a = this.r() * Math.PI * 2, rad = 4 + this.r() * 14;
          const x = Math.round(W / 2 + Math.cos(a) * rad * 1.8), y = Math.round(6 + Math.sin(a) * rad * 0.35);
          if (y >= 1 && y < 12) this.sparks.push([x, y, 0, pick(this.r, SPARK_STYLES), "*"]);
        }
        for (const s of this.sparks) { s[2] += 1; s[4] = "*+·."[Math.min(s[2] >> 1, 3)]; }
        this.sparks = this.sparks.filter((s) => s[2] < 8);
      }
    }
    *hold(n) { for (let i = 0; i < n; i++) { this.tick(); yield this.draw(); } }
    *bet(name, act, words, amount) {
      this.acts[name] = act;
      this.ticker = ["ink", `${name} ${words}`];
      if (amount) {
        this.stack[name] -= amount; this.pot += amount;
        for (let k = 0; k < Math.min(3, 1 + Math.floor(amount / 20)); k++) this.chips.push([44 + k * 2, SEATS[name], W / 2 - 1 + k, 6, -0.2 * k]);
      }
      yield* this.hold(9);
    }
    *deal(...idx) {
      this.acts = { pairs: "", "l5-shark": "" };
      for (const i of idx) this.board[i] = "back";
      yield* this.hold(3);
      for (let f = 0; f < idx.length * 2 + 2; f++) {
        idx.forEach((i, k) => { if (f === k * 2) this.board[i] = "flip"; else if (f === k * 2 + 1) this.board[i] = "face"; });
        yield* this.hold(1);
      }
      yield* this.hold(6);
    }
  }

  function* showdown(seed = 7) {
    const t = new Table(seed);
    yield* t.hold(10);
    yield* t.bet("pairs", "R 6", "raises to 6", 6);
    yield* t.bet("l5-shark", "C", "calls", 6);
    yield* t.deal(0, 1, 2);
    yield* t.bet("l5-shark", "R 8", "bets 8", 8);
    yield* t.bet("pairs", "C", "calls", 8);
    yield* t.deal(3);
    yield* t.bet("l5-shark", "R 20", "bets 20 (68% equity, it thinks)", 20);
    yield* t.bet("pairs", "R 60", "raises to 60", 60);
    yield* t.bet("l5-shark", "C", "calls", 40);
    yield* t.deal(4);
    yield* t.bet("l5-shark", "X", "checks", 0);
    yield* t.bet("pairs", "X", "checks", 0);
    t.ticker = ["muted", "showdown"];
    t.flip["l5-shark"] = 1;
    yield* t.hold(3);
    t.flip["l5-shark"] = 0; t.shown["l5-shark"] = true;
    yield* t.hold(8);
    t.winner = "pairs";
    t.stack.pairs += t.pot;
    t.acts = { pairs: `+${t.pot}`, "l5-shark": "pair of aces" };
    t.ticker = ["gold", `pairs wins ${t.pot} · three kings beat the shark's aces`];
    yield* t.hold(36);
  }

  // --- player ---
  const FPS = 20;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    let last; for (last of showdown(7)); // the finished hand, no motion
    el.innerHTML = last.html();
    return;
  }
  let stats = [null, null];
  if (el.dataset && el.dataset.api && window.fetch) {
    fetch(`${el.dataset.api}/api/leaderboard`).then((r) => r.json()).then((d) => {
      stats = [d.players.length, d.players.reduce((sum, p) => sum + (p.hands || 0), 0)];
    }).catch(() => {});
  }
  const SCENES = [(s) => splash(s), (s) => arena(stats[0], stats[1], s), (s) => showdown(s)];
  let visible = true, scene = 0, frames = splash(7), timer = null, seed = 7;
  function step() {
    timer = null;
    if (!visible || document.hidden) return;
    const next = frames.next();
    if (next.done) {
      scene = (scene + 1) % SCENES.length;
      if (scene === 0) seed += 1;
      frames = SCENES[scene](seed);
      timer = setTimeout(step, 1200); // beat between scenes
      return;
    }
    el.innerHTML = next.value.html();
    timer = setTimeout(step, 1000 / FPS);
  }
  const resume = () => { if (!timer && visible && !document.hidden) step(); };
  new IntersectionObserver((entries) => { visible = entries[0].isIntersecting; resume(); }).observe(el);
  document.addEventListener("visibilitychange", resume);
  resume();
})();
