// Moments: the things in a race worth a glance, found by walking F1's timing updates in order.
// Pure functions, no DOM: the page calls computeMoments(D) once after loading, and tests/test_moments.py
// runs the same file under node. Every moment is {id, t, kind, nums, text, score, life, lap}:
//   t     session ms (same clock as the rest of the replay file)
//   score 0..1, how much it deserves attention (weights are all in WEIGHTS below, tune them there)
//   life  how many ms the Director keeps it in focus
(function (root) {
  "use strict";

  const WEIGHTS = {
    lead: 1.0,          // the race lead changes hands
    redflag: 1.0,
    safetycar: 0.9,
    vsc: 0.75,
    restart: 0.6,
    pass: 0.7,          // position change, scaled by where it happened
    battle: 0.65,       // two cars within a second for a few laps
    penalty: 0.7,
    investigation: 0.5,
    retire: 0.6,
    fastest: 0.4,
    pit: 0.35,          // scaled by where the car is running
    radio: 0.2,
    start: 0.8,
  };
  const LIFE = { battle: 25000, pass: 14000, lead: 16000, safetycar: 20000, vsc: 18000, redflag: 20000 };
  const DEFAULT_LIFE = 14000;

  const SAMPLE_MS = 5000;        // running order is checked this often
  const PASS_HOLD_MS = 20000;    // a pass counts only if it is still true this long afterwards
  const BATTLE_GAP_S = 1.0;      // within this of the car ahead...
  const BATTLE_HOLD_MS = 150000; // ...for this long makes a battle (about two laps)
  const BATTLE_BREAK_S = 1.6;    // the battle is over when the gap opens past this
  const BATTLE_COOLDOWN_MS = 600000;
  const MAX_PASSES_PER_SAMPLE = 6;
  const TOP = 10;                // overtakes and battles are only reported inside the top ten

  // F1 sends partial updates: objects merge key by key, arrays are replaced
  function merge(target, src) {
    if (src === null || typeof src !== "object") return src;
    if (Array.isArray(src)) return src.map(x => merge(undefined, x));
    if (target === null || typeof target !== "object") target = {};
    for (const k in src) target[k] = merge(target[k], src[k]);
    return target;
  }

  const posFactor = p => p <= 3 ? 1 : p <= 5 ? 0.8 : p <= 10 ? 0.55 : 0.25;
  const seconds = v => {
    const s = typeof v === "string" ? v.replace("+", "") : v;
    return /^\d+(\.\d+)?$/.test(String(s)) ? parseFloat(s) : null;
  };

  function namer(D) {
    const names = {};
    for (const [, upd] of D.streams.DriverList || []) {
      for (const [num, d] of Object.entries(upd || {})) {
        if (d && typeof d === "object") names[num] = { ...names[num], ...d };
      }
    }
    return {
      last: n => names[n]?.LastName || names[n]?.Tla || `Car ${n}`,
      tla: n => names[n]?.Tla || n,
      numOfTla: t => Object.keys(names).find(n => names[n].Tla === t),
    };
  }

  function computeMoments(D) {
    const S = D.streams || {};
    const who = namer(D);
    const out = [];
    const add = (t, kind, nums, text, score, lap) => out.push({
      t, kind, nums, text, score: Math.max(0, Math.min(1, score)), lap: lap ?? null,
      life: LIFE[kind] || DEFAULT_LIFE,
    });

    const startUpd = (S.SessionStatus || []).find(([, s]) => s.Status === "Started");
    const raceStart = startUpd ? startUpd[0] : null;
    if (raceStart === null) return [];  // no green light, no race to describe
    const endUpd = (S.SessionStatus || []).find(([, s]) => s.Status === "Finished");
    const raceEnd = endUpd ? endUpd[0] : (D.meta && D.meta.end) || Infinity;
    const inRace = t => t >= raceStart && t <= raceEnd;

    // ---- one pass over the timing updates, keeping the running state ----
    const timing = S.TimingData || [];
    const stats = S.TimingStats || [];
    const laps = S.LapCount || [];
    let state = {}, statsState = {}, lapState = {};
    let ti = 0, si = 0, li = 0;
    const advance = T => {
      while (ti < timing.length && timing[ti][0] <= T) state = merge(state, timing[ti++][1]);
      while (si < stats.length && stats[si][0] <= T) statsState = merge(statsState, stats[si++][1]);
      while (li < laps.length && laps[li][0] <= T) lapState = merge(lapState, laps[li++][1]);
    };
    const lines = () => state.Lines || {};
    const orderNow = () => {
      const L = lines();
      return Object.keys(L)
        .filter(n => L[n] && L[n].Line != null && !L[n].Retired)
        .sort((a, b) => L[a].Line - L[b].Line);
    };
    const inPits = n => !!(lines()[n]?.InPit || lines()[n]?.PitOut);
    const lapAt = () => lapState.CurrentLap ?? null;
    const lapOf = t => {  // the lap in progress at session time t
      let lo = 0, hi = laps.length - 1, found = null;
      while (lo <= hi) { const mid = (lo + hi) >> 1; if (laps[mid][0] <= t) { found = laps[mid][1].CurrentLap ?? found; lo = mid + 1; } else hi = mid - 1; }
      return found;
    };

    const lastEnd = Math.min(raceEnd, (D.meta && D.meta.end) || raceEnd);
    const retired = new Set();
    let fastestHolder = null;
    const battle = {};  // "ahead:behind" -> {since, fired, last}
    let prev = null;    // previous sample: {order, pits}
    const pending = []; // passes waiting out PASS_HOLD_MS
    const settled = [];

    for (let T = raceStart; T <= lastEnd; T += SAMPLE_MS) {
      advance(T);
      const order = orderNow();
      const rank = {};
      order.forEach((n, i) => { rank[n] = i + 1; });
      const L = lines();

      // passes: someone who was behind another driver is now ahead, and neither was in the pits
      if (prev && T - raceStart > 60000) {  // the first minute is the start-line shuffle
        const found = [];
        for (const a of order) {
          const ra = rank[a], rpa = prev.rank[a];
          if (rpa == null || ra >= rpa) continue;
          for (const b of order) {
            if (b === a) continue;
            const rb = rank[b], rpb = prev.rank[b];
            if (rpb != null && rpb < rpa && rb > ra && ra <= TOP && !prev.pits.has(a) && !prev.pits.has(b) && !inPits(a) && !inPits(b)) {
              found.push({ t: T, a, b, ra });
            }
          }
        }
        // a dozen "passes" in one sample is the timing board re-sorting (a restart, a pit-lane
        // start, a data catch-up), not racing
        if (found.length <= MAX_PASSES_PER_SAMPLE) {
          for (const f of found) {
            // the cars swapping straight back means the first swap was a wobble, not a pass
            const undone = pending.findIndex(q => q.a === f.b && q.b === f.a);
            if (undone >= 0) pending.splice(undone, 1); else pending.push(f);
          }
        }
      }
      for (let i = pending.length - 1; i >= 0; i--) {
        const p = pending[i];
        if (T - p.t < PASS_HOLD_MS) continue;
        pending.splice(i, 1);
        if (rank[p.a] != null && rank[p.b] != null && rank[p.a] < rank[p.b] && rank[p.a] <= TOP + 1) {
          settled.push(p);
        }
      }
      // one moment per driver per sample, however many cars he went by
      const byPasser = {};
      for (const p of settled.splice(0)) (byPasser[p.a + "@" + p.t] = byPasser[p.a + "@" + p.t] || []).push(p);
      for (const group of Object.values(byPasser)) {
        const { t, a, ra } = group[0];
        const victims = group.map(g => g.b);
        const names = victims.length === 1 ? who.last(victims[0])
          : victims.slice(0, -1).map(who.last).join(", ") + " and " + who.last(victims[victims.length - 1]);
        const lap = lapOf(t);
        if (ra === 1) add(t, "lead", [a, ...victims], `${who.last(a)} takes the lead from ${who.last(victims[victims.length - 1])}`, WEIGHTS.lead, lap);
        else add(t, "pass", [a, ...victims], `${who.last(a)} passes ${names} for P${ra}`, WEIGHTS.pass * posFactor(ra), lap);
      }

      // battles: interval to the car ahead under a second, held for a while
      const seen = new Set();
      order.forEach((n, i) => {
        if (i === 0 || i >= TOP) return;
        const ahead = order[i - 1];
        const gap = seconds(L[n]?.IntervalToPositionAhead?.Value);
        const key = `${ahead}:${n}`;
        seen.add(key);
        if (gap === null || inPits(n) || inPits(ahead) || gap > BATTLE_BREAK_S) { delete battle[key]; return; }
        const b = battle[key] || (battle[key] = { since: T, lap: lapAt(), fired: false, last: -Infinity });
        if (gap > BATTLE_GAP_S) { b.since = null; return; }  // still near, but the clock restarts
        if (b.since === null) { b.since = T; b.lap = lapAt(); }
        if (!b.fired && T - b.since >= BATTLE_HOLD_MS && T - b.last >= BATTLE_COOLDOWN_MS) {
          b.fired = true; b.last = T;
          const lapsClose = Math.max(2, (lapAt() ?? 0) - (b.lap ?? 0));
          add(T, "battle", [n, ahead], `${who.last(n)} is ${gap.toFixed(1)} s behind ${who.last(ahead)}, and has been within a second for ${lapsClose} laps`,
            WEIGHTS.battle * posFactor(i + 1), lapAt());
        }
      });
      for (const key of Object.keys(battle)) if (!seen.has(key)) delete battle[key];

      // retirements
      for (const n of Object.keys(L)) {
        if ((L[n].Retired || L[n].Stopped) && !retired.has(n)) {
          retired.add(n);
          const wasRank = prev && prev.rank[n];
          add(T, "retire", [n], `${who.last(n)} is out of the race`, WEIGHTS.retire * posFactor(wasRank || 20), lapAt());
        }
      }

      // fastest lap, once the field has settled
      const lapNow = lapAt();
      if (lapNow !== null && lapNow >= 3) {
        const holder = Object.keys(statsState.Lines || {}).find(n => +statsState.Lines[n]?.PersonalBestLapTime?.Position === 1);
        if (holder && holder !== fastestHolder) {
          if (fastestHolder !== null) {
            add(T, "fastest", [holder], `${who.last(holder)} sets the fastest lap, ${statsState.Lines[holder].PersonalBestLapTime.Value}`, WEIGHTS.fastest, lapNow);
          }
          fastestHolder = holder;
        }
      } else {
        const holder = Object.keys(statsState.Lines || {}).find(n => +statsState.Lines[n]?.PersonalBestLapTime?.Position === 1);
        if (holder) fastestHolder = holder;
      }

      prev = { rank, pits: new Set(order.filter(inPits)) };
    }

    add(raceStart, "start", [], "Lights out", WEIGHTS.start, 1);

    // ---- pit stops, with the running order at the time ----
    state = {}; ti = 0; statsState = {}; si = 0; lapState = {}; li = 0;
    for (const stop of D.pitStops || []) {
      if (!inRace(stop.t)) continue;
      advance(stop.t);
      const order = orderNow();
      const p = order.indexOf(stop.num) + 1 || 20;
      const secs = stop.stop ? ` (${stop.stop} s)` : "";
      add(stop.t, "pit", [stop.num], `${who.last(stop.num)} pits from P${p}${secs}`, WEIGHTS.pit * posFactor(p), +stop.lap || lapAt());
    }

    // ---- track status: safety car, VSC, red flag, and racing resuming ----
    let neutral = null;
    for (const [t, s] of S.TrackStatus || []) {
      if (!inRace(t)) continue;
      const st = String(s.Status);
      if (st === "4") { add(t, "safetycar", [], "Safety car", WEIGHTS.safetycar, lapOf(t)); neutral = "sc"; }
      else if (st === "6") { add(t, "vsc", [], "Virtual safety car", WEIGHTS.vsc, lapOf(t)); neutral = "vsc"; }
      else if (st === "5") { add(t, "redflag", [], "Red flag: the race is stopped", WEIGHTS.redflag, lapOf(t)); neutral = "red"; }
      else if (st === "1" && neutral) {
        add(t, "restart", [], neutral === "red" ? "Racing is about to resume" : "Track clear: racing resumes", WEIGHTS.restart, lapOf(t));
        neutral = null;
      }
    }

    // ---- stewards: penalties and investigations that are about to be decided ----
    for (const m of D.raceControl || []) {
      if (!inRace(m.t)) continue;
      const msg = String(m.Message || "");
      if (!/^FIA STEWARDS:/.test(msg) || /PENALTY SERVED|NO FURTHER|WILL BE INVESTIGATED AFTER|NO INVESTIGATION|DELETED/.test(msg)) continue;
      const tla = (msg.match(/\(([A-Z]{3})\)/) || [])[1];
      const num = tla ? who.numOfTla(tla) : null;
      const nums = num ? [num] : [];
      const name = num ? who.last(num) : "A driver";
      const cause = (msg.match(/ - ([A-Z ,'-]+?)(?: \(\d\d:\d\d:\d\d\))?$/) || [])[1];
      const why = cause ? `, ${cause.toLowerCase()}` : "";
      const pen = msg.match(/(\d+) SECOND (?:TIME )?PENALTY/) || msg.match(/(DRIVE THROUGH|STOP\/GO)/);
      if (/PENALTY/.test(msg) && pen) {
        add(m.t, "penalty", nums, `${name} gets a ${/^\d+$/.test(pen[1]) ? pen[1] + "-second" : pen[1].toLowerCase()} penalty${why}`, WEIGHTS.penalty, m.Lap);
      } else if (/UNDER INVESTIGATION|NOTED/.test(msg)) {
        add(m.t, "investigation", nums, `${name} is under investigation${why}`, WEIGHTS.investigation, m.Lap);
      }
    }

    // ---- team radio (low weight: it is there to be seen, not chased) ----
    for (const r of D.radio || []) {
      if (!inRace(r.t)) continue;
      add(r.t, "radio", [String(r.RacingNumber)], `Team radio: ${who.last(String(r.RacingNumber))}`, WEIGHTS.radio, null);
    }

    out.sort((a, b) => a.t - b.t || b.score - a.score);
    out.forEach((m, i) => { m.id = i; });
    return out;
  }

  // The Director's choice at time T: the best moment still in its window, held for minDwell
  // so the focus doesn't flicker. `current` is what was chosen last call (or null).
  function pickFocus(moments, T, current, minDwell) {
    const live = moments.filter(m => m.t <= T && T < m.t + m.life && m.kind !== "radio");
    if (current && T >= current.since && T - current.since < minDwell && live.some(m => m.id === current.id)) return current;
    if (!live.length) return null;
    const best = live.reduce((a, b) => (b.score > a.score || (b.score === a.score && b.t > a.t)) ? b : a);
    return current && current.id === best.id ? current : { id: best.id, since: T };
  }

  const api = { computeMoments, pickFocus, WEIGHTS, merge };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Moments = api;
})(typeof window !== "undefined" ? window : globalThis);
