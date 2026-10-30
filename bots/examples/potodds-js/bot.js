// Pot-odds bot in plain Node.js (no dependencies): rates its hand, then calls only when the
// price is right and raises its strong hands. Shows the protocol from a non-Python language.
const readline = require("readline");
const RANKS = "23456789TJQKA";

function strength(hole, board) {
  // 0..1: preflop from high cards / pair / suited; postflop bumps for pairs and better.
  const r = hole.map((c) => RANKS.indexOf(c[0]));
  let s = (r[0] + r[1]) / 24;
  if (r[0] === r[1]) s += 0.35;
  if (hole[0][1] === hole[1][1]) s += 0.06;
  if (board.length) {
    const counts = {};
    for (const c of hole.concat(board)) counts[c[0]] = (counts[c[0]] || 0) + 1;
    const mine = hole.map((c) => counts[c[0]]);
    const best = Math.max(...mine);
    if (best >= 3) s += 0.5;
    else if (mine[0] >= 2 && mine[1] >= 2 && hole[0][0] !== hole[1][0]) s += 0.4;
    else if (best === 2) s += 0.2;
  }
  return Math.min(s, 1);
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const msg = JSON.parse(line);
  if (msg.type !== "action_request") return;
  const s = strength(msg.hole, msg.board);
  const odds = msg.to_call / (msg.pot + msg.to_call || 1);
  let reply;
  if (s > 0.75 && msg.legal.includes("R")) {
    reply = "R " + Math.min(msg.min_raise + Math.floor(msg.pot / 2), msg.max_raise);
  } else if (msg.legal.includes("X")) {
    reply = "X";
  } else {
    reply = s >= odds + 0.15 ? "C" : "F";
  }
  process.stdout.write(reply + "\n");
});
