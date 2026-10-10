// 光の目安：日の出・日の入り・ゴールデンアワー・南中と、太陽の方角（地点ページ）
// 太陽の位置は suncalc と同じ近似式（精度は1分程度）。時刻は日本時間で表示
(function () {
  const rad = Math.PI / 180, dayMs = 864e5, J1970 = 2440588, J2000 = 2451545, e = rad * 23.4397;
  const toDays = d => d.valueOf() / dayMs - 0.5 + J1970 - J2000;
  const decl = (l, b) => Math.asin(Math.sin(b) * Math.cos(e) + Math.cos(b) * Math.sin(e) * Math.sin(l));
  const ra = (l, b) => Math.atan2(Math.sin(l) * Math.cos(e) - Math.tan(b) * Math.sin(e), Math.cos(l));
  function sunPos(date, lat, lng) {
    const d = toDays(date), M = rad * (357.5291 + 0.98560028 * d);
    const C = rad * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
    const L = M + C + rad * 102.9372 + Math.PI;
    const dec = decl(L, 0), r = ra(L, 0);
    const th = rad * (280.16 + 360.9856235 * d) - (-lng * rad);
    const H = th - r, phi = rad * lat;
    const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
    let az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi)); // 南が0
    az = (az / rad + 180 + 360) % 360;                                                             // 北が0・時計回り
    return { alt: alt / rad, az };
  }
  const DIRS = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東', '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西'];
  const dir = az => DIRS[Math.round(az / 22.5) % 16] + ' ' + Math.round(az) + '°';
  const hm = d => d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo' });

  function compute(box) {
    const lat = +box.dataset.lat, lon = +box.dataset.lon;
    const input = box.querySelector('.sun-date');
    const day = input.value ? new Date(input.value + 'T00:00:00+09:00') : new Date();
    const start = new Date(day.toLocaleDateString('en-CA', { timeZone: 'Asia/Tokyo' }) + 'T00:00:00+09:00');
    const pts = [];
    for (let m = 0; m <= 1440; m++) { const t = new Date(start.valueOf() + m * 6e4); pts.push({ t, ...sunPos(t, lat, lon) }); }
    const cross = (h, rising) => {
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1].alt, b = pts[i].alt;
        if (rising ? (a < h && b >= h) : (a >= h && b < h)) return pts[i];
      }
      return null;
    };
    const noon = pts.reduce((x, y) => (y.alt > x.alt ? y : x));
    const rows = [
      ['夜明け（薄明の始まり）', cross(-6, true)],
      ['日の出', cross(-0.833, true), true],
      ['朝のゴールデンアワー', cross(6, true), false, '〜'],
      ['南中', noon, false, '', ' 高度 ' + Math.round(noon.alt) + '°'],
      ['夕方のゴールデンアワー', cross(6, false), false, '〜'],
      ['日の入り', cross(-0.833, false), true],
      ['夕暮れ（薄明の終わり）', cross(-6, false)],
    ];
    const dl = box.querySelector('.sun');
    dl.textContent = '';
    rows.forEach(([label, p, showDir, prefix, extra]) => {
      const dt = document.createElement('dt'); dt.textContent = label;
      const dd = document.createElement('dd');
      if (!p) dd.textContent = '—';
      else {
        dd.textContent = (label === '朝のゴールデンアワー' ? '日の出〜' + hm(p.t) :
                          label === '夕方のゴールデンアワー' ? hm(p.t) + '〜日の入り' : hm(p.t))
                       + (showDir ? '　方角 ' + dir(p.az) : '') + (extra || '');
      }
      dl.append(dt, dd);
    });
  }
  document.querySelectorAll('.sunbox').forEach(box => {
    const input = box.querySelector('.sun-date');
    input.value = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Tokyo' });
    input.addEventListener('change', () => compute(box));
    compute(box);
  });
})();
