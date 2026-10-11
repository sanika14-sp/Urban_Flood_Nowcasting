// Fallback data (storm from data/rainfall_event.csv, depths from the model) if the API is unreachable.
const FB = {
    rain: [10, 35, 65, 85, 75, 55, 40, 25, 15, 8, 3, 1, 0],
    sum: [[2.8, 0, 0], [7.3, 0, 0], [22.3, 0, 5], [43.5, 5, 5], [59.5, 6, 5], [65.8, 7, 4], [63.5, 7, 4], [61.2, 7, 3], [58.8, 6, 3], [56.5, 6, 1], [54.1, 5, 2], [51.8, 5, 2], [49.5, 5, 2]]
};
const $ = id => document.getElementById(id), N = 13, W = 900, H = 330, L = 44, R = 16, TOP = 12, BOT = 300;
let rain = FB.rain, sum = FB.sum, live = false;
const x = i => L + (W - L - R) * i / (N - 1);
const yD = d => BOT - (d / 90) * 190;
function draw(i) {
    const bw = (W - L - R) / (N - 1) * .62; let s = '';
    [0, 30, 60, 90].forEach(d => { s += `<line x1="${L}" x2="${W - R}" y1="${yD(d)}" y2="${yD(d)}" stroke="var(--line)"/><text x="${L - 8}" y="${yD(d) + 4}" text-anchor="end">${d}</text>` });
    s += `<text x="${L}" y="${H - 4}">0</text><text x="${x(6)}" y="${H - 4}" text-anchor="middle">90 min</text><text x="${W - R}" y="${H - 4}" text-anchor="end">180 min</text>`;
    s += `<text x="${L + 6}" y="${yD(90) - 4}">depth, cm</text>`;
    rain.forEach((v, k) => { s += `<rect x="${x(k) - bw / 2}" y="${TOP}" width="${bw}" height="${v / 90 * 100}" fill="var(--rain)" opacity="${k === i ? 1 : .45}"/>` });
    const pts = sum.map((v, k) => `${x(k)},${yD(v[0])}`);
    s += `<polygon points="${L},${BOT} ${pts.join(' ')} ${W - R},${BOT}" fill="var(--water)" opacity=".28"/><polyline points="${pts.join(' ')}" fill="none" stroke="var(--water)" stroke-width="3"/>`;
    s += `<line x1="${x(i)}" x2="${x(i)}" y1="${TOP}" y2="${BOT}" stroke="var(--ink)" stroke-width="2"/><circle cx="${x(i)}" cy="${yD(sum[i][0])}" r="6" fill="var(--water)" stroke="var(--paper)" stroke-width="2"/>`;
    s += `<text x="${W - R}" y="${TOP + 10}" text-anchor="end">rain, mm per hour (hangs from the top)</text>`;
    $('chart').innerHTML = s;
    $('rT').textContent = i * 15 + ' min'; $('rR').textContent = rain[i]; $('rD').textContent = sum[i][0];
    $('cR').textContent = sum[i][1]; $('cO').textContent = sum[i][2];
}
function go() { draw(+$('t').value / 15) }
$('t').addEventListener('input', go);
(async () => {
    go();
    try {
        const r = await (await fetch('/api/rainfall')).json();
        const ss = await Promise.all([...Array(N).keys()].map(k => fetch('/api/summary?t=' + k * 15).then(q => q.json())));
        rain = r.time_series.map(p => p.intensity_mm_per_hr);
        sum = ss.map(s => [s.max_depth_cm, s.breakdown.impassable_red, s.breakdown.risk_orange]);
        $('evt').textContent = r.event_name + ', peak ' + r.peak_intensity_mm_hr + ' mm per hour'; live = true;
    } catch (e) { }
    $('src').textContent = live ? 'Values come from the running model.' : 'The model is not reachable, so this shows sample values.';
    go();
})();